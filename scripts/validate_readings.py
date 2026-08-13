"""A blind re-read of a decision, and the disagreement policy over its verdict.

There is no model SDK and no API key in this repository, so the model is not
called from here. Instead this module writes **blind task files** (document
text in, nothing else), a separate agent reads one and writes a **verdict
file** (a rate and the exact quoted span it came from), and this module
**ingests** that verdict and compares it, in code, against the deterministic
readers' own result.

The rule that decides whether any of this is worth building: never ask the
model "is 3% correct?". Ask it "what rate does this text set?", blind to the
parser's answer, and compare afterwards. A model shown a number agrees with
it, because agreement is the plausible continuation — that turns the check
into a rubber stamp that passes on exactly the rows that are wrong. So the
blinding here is structural, not a promise: `task_fields()` below has no
parameter through which a parser's rate, reading, or `decision_ref` could
reach the file a model is handed. `tests/test_validation_leak.py` proves that
a naive version — one that just dumps the whole extracted row in "for
context" — fails the check this module exists to pass.

**The model may veto a rate and may break a tie between disagreeing readers.
It may never originate one.** A parser that found nothing while a verdict
reports a rate is an escalation, not a publication — see
`compute_results()`. A verdict disagreeing with an already-published row is
flagged and retained, never removed: a bad model day must not delete a
district that carries a real citation.

No function here writes `data/rates.csv`. Verdicts are read from a file
written by a separate process, never computed at build time, so a rebuild of
`dist/` stays byte-reproducible and any past verdict stays auditable.

## `data/validation-tasks/` is regenerable, and gitignored

The task files `--emit` writes are reconstructable from `.cache/documents/`
(gitignored itself, reconstructable from the site) plus this module — there is
nothing in a task file that is not derived from a document already tracked
elsewhere. So the directory is NOT committed (see `.gitignore`). This has a
consequence a caller must respect: **on a fresh clone, `--ingest` cannot
verify any `quoted_span` until `--emit` has been run first.** A verdict whose
span cannot be checked is reported as `cannot_verify`, never silently folded
into `refused` — see `compute_results()` — and `ingest()` refuses to write
`data/validation-results.json` at all while any exist, because a results file
claiming a clean bill of health while every verdict actually went unchecked is
worse than no results file.
"""

from __future__ import annotations

import csv
import hashlib
import inspect
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from extract_rates import BASE_URL, EXTRACTED, QUEUE, WouldShrinkDataset, cached_document, pdf_text
from validate import RATE_MAX, RATE_MIN, RATES_CSV, REPO_ROOT

TASKS_DIR = REPO_ROOT / "data" / "validation-tasks"
VERDICTS = REPO_ROOT / "data" / "validation-verdicts.json"
VERDICTS_DIR = REPO_ROOT / "data" / "validation-verdicts.d"
RESULTS = REPO_ROOT / "data" / "validation-results.json"
UNAVAILABLE_LOG = REPO_ROOT / "data" / "validation-unavailable.json"


def task_fields(
    document_id: str, source_url: str, text: str, kazakh_text: str | None
) -> dict[str, Any]:
    """Assemble a blind task, and nothing more than a blind task.

    This is the whole blinding boundary: every value in the returned dict
    comes from the document itself (its id, its own URL, its own extracted
    text) and none from any parser's reading of it. No parameter here could
    carry a rate, a reading, or a `decision_ref` even if a caller tried to
    pass one — there is no such parameter to pass it through.
    """
    return {
        "document_id": document_id,
        "source_url": source_url,
        "text": text,
        "kazakh_text": kazakh_text,
    }


# The complete set of fields a task file may ever carry. Anything else is a
# leak: it hands the reading agent information beyond the document itself.
# **Derived from `task_fields`'s own signature, not typed to agree with it.**
# `TASK_FIELDS`, the dict `task_fields` returns, and a test's expected
# parameter set used to be three hand-typed constants — one edit adding a
# field to two of them would have reopened the leak with every test green.
# This is the one place that cannot drift from the function it describes.
TASK_FIELDS = frozenset(inspect.signature(task_fields).parameters)

# Outcomes of comparing a verdict against the deterministic result, applying
# "the model may veto and break a tie, and may never originate a rate."
AGREE = "agree"
BLOCKED = "blocked"
FLAGGED = "flagged"
ESCALATED = "escalated"
# A published row whose parser support has been withdrawn: the model read a
# rate, but the parser that used to confirm this document no longer does (a
# reader was tightened, e.g. the regime check, and it now refuses). This is
# the one outcome a human MUST act on — the row is live in data/rates.csv
# right now on support that no longer exists — so it gets its own name rather
# than sharing ESCALATED's "a new candidate the parser missed" framing, which
# reads as nothing to worry about.
PUBLISHED_SUPPORT_WITHDRAWN = "published_support_withdrawn"
REFUSED = "refused"
# The checker could not run — never a `refused` and never a model behaviour.
# `data/validation-tasks/<id>.json` does not exist for this document, so
# `quoted_span` could not be checked against anything, and a verdict that
# was never checked must never look like one that passed.
CANNOT_VERIFY = "cannot_verify"

DOCUMENT_ID_IN_URL = re.compile(r"/docs/([A-Za-z0-9]+)/?$")


class CannotVerify(RuntimeError):
    """Some verdicts could not be checked against their document text at all."""


def clear_tasks_dir() -> None:
    """Remove every existing task file before a full re-emission.

    `TASKS_DIR` was never cleared between runs. A document that used to be in
    the emitted population and later dropped out — or whose cached text
    changed — left its OLD task file sitting there indefinitely, so a verdict
    submitted against current text could end up checked against text nobody
    currently serves. Called only from a full, unfiltered `--emit` run (see
    `main()`); an explicit id list is a narrow, additive request and must not
    wipe tasks it was not asked to touch.
    """
    if not TASKS_DIR.exists():
        return
    for path in TASKS_DIR.glob("*.json"):
        path.unlink()


def emit_tasks(document_ids: list[str]) -> tuple[list[str], list[str]]:
    """Write one blind task file per fetchable document.

    A document whose text cannot be obtained gets no task file at all — a
    missing document is a transport failure, not a reading, and a task with
    empty text would invite an answer from prior knowledge rather than from
    the page. Returns (emitted, unavailable), both sorted.

    The `unavailable` list is also persisted to `UNAVAILABLE_LOG` — printing
    it and discarding it (the previous behaviour) made a document that could
    not be fetched indistinguishable, one run later, from a document nobody
    had ever tried to process. `report()` reads it back.
    """
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    emitted: list[str] = []
    unavailable: list[str] = []

    for document_id in document_ids:
        try:
            _, payload = cached_document(document_id)
            text = pdf_text(payload)
        except Exception:  # noqa: BLE001 — any fetch/parse failure is UNAVAILABLE, not a reading
            unavailable.append(document_id)
            continue
        if not text.strip():
            unavailable.append(document_id)
            continue

        kazakh_text: str | None
        try:
            _, kazakh_payload = cached_document(document_id, language="kaz")
            kazakh_text = pdf_text(kazakh_payload)
        except Exception:  # noqa: BLE001 — a missing Kazakh copy narrows the task, not a refusal
            kazakh_text = None

        task = task_fields(
            document_id=document_id,
            source_url=f"{BASE_URL}/rus/docs/{document_id}",
            text=text,
            kazakh_text=kazakh_text,
        )
        (TASKS_DIR / f"{document_id}.json").write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        emitted.append(document_id)

    emitted_sorted, unavailable_sorted = sorted(emitted), sorted(unavailable)
    UNAVAILABLE_LOG.write_text(
        json.dumps({"unavailable": unavailable_sorted}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return emitted_sorted, unavailable_sorted


def load_unavailable() -> list[str]:
    """Documents the most recent `--emit` run could not fetch a task for."""
    if not UNAVAILABLE_LOG.exists():
        return []
    data = json.loads(UNAVAILABLE_LOG.read_text(encoding="utf-8"))
    unavailable: list[str] = data.get("unavailable", [])
    return unavailable


def load_queue_ids() -> list[str]:
    """document ids sitting in `data/extraction-queue.json` — queued, not confirmed.

    `--emit`'s default only covers confirmed rows (`load_parser_rates()`),
    because that is where a parser answer already exists to compare against.
    But the queue — including a `terminal` regime refusal — is where a second
    opinion is worth the most, and it was never reachable at all. `--population
    queued` or `--population all` on `--emit` reaches it now.
    """
    if not QUEUE.exists():
        return []
    queue = json.loads(QUEUE.read_text(encoding="utf-8")).get("pending", [])
    return sorted({entry["document_id"] for entry in queue if entry.get("document_id")})


def load_verdicts() -> list[dict[str, Any]]:
    """The verdict file, or an empty list where none has been written yet."""
    if not VERDICTS.exists():
        return []
    data = json.loads(VERDICTS.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{VERDICTS} must hold a JSON list of verdicts")
    return data


def load_parser_rates() -> dict[str, float]:
    """document_id -> the confirmed rate the deterministic readers reached.

    Only `confirmed` rows are here at all — that is what `data/extracted-rates.json`
    already holds. A document absent from this mapping is one the parsers were
    silent on, which is exactly the case `compute_results()` must treat as an
    escalation rather than a publication when a verdict reports a rate for it.
    """
    if not EXTRACTED.exists():
        return {}
    data = json.loads(EXTRACTED.read_text(encoding="utf-8"))
    return {row["document_id"]: row["rate"] for row in data.get("rows", [])}


def published_document_ids(path: Path) -> set[str]:
    """document ids already carrying a row in data/rates.csv.

    Read from each row's `source_url`, the only field that names the document
    — `data/rates.csv` never stores a document id directly.
    """
    if not path.exists():
        return set()
    ids: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            match = DOCUMENT_ID_IN_URL.search(row.get("source_url", ""))
            if match:
                ids.add(match.group(1))
    return ids


def _task_text(document_id: str) -> str | None:
    """The text a verdict's quoted span must be checked against, or None."""
    path = TASKS_DIR / f"{document_id}.json"
    if not path.exists():
        return None
    task = json.loads(path.read_text(encoding="utf-8"))
    text: str | None = task.get("text")
    return text


def verdict_unverifiable(verdict: dict[str, Any]) -> bool:
    """True when the checker cannot run against this verdict at all.

    Distinct from every other objection `validate_verdict` raises: those are
    about what the MODEL did (a rate stored as a percent, a fabricated span).
    This is about whether `data/validation-tasks/<id>.json` exists to check
    against, which is a fact about the PIPELINE, not the model, and it must
    never be folded into `refused` — a caller reading `refused` counts would
    otherwise read "the model declined this many times" when the true count
    is "this many verdicts were never checked at all".
    """
    if verdict.get("refused"):
        return False
    if verdict.get("rate") is None:
        return False
    return _task_text(verdict.get("document_id", "")) is None


def validate_verdict(verdict: dict[str, Any]) -> str | None:
    """A structural objection to this verdict, or None if it is well-formed.

    Four checks, each guarding a specific way a verdict lies about having
    read the document:

    - a refused verdict needs nothing else — refusal is itself the answer;
    - a rate outside the statutory band (`validate.RATE_MIN`..`RATE_MAX`) is
      rejected outright — this catches not only the single most likely
      data-entry error (storing 3 for 3%) but also 0, a negative number, and
      1.0, none of which a bare `rate > 1` clamp would have caught;
    - a rate with no quoted span, or a quoted span that does not actually
      appear in the document's own text, means the model produced the span
      rather than read it — and that verdict must be refused, not trusted;
    - a quoted span that does not even contain the rate's own digits passes
      the "appears in the text" check on any unrelated substring — a header,
      a district's name — so the span must also carry the number it is meant
      to attest to.
    """
    if verdict.get("refused"):
        return None

    rate = verdict.get("rate")
    if rate is None:
        return "not marked refused, but carries no rate"
    if not isinstance(rate, int | float) or isinstance(rate, bool):
        return f"rate {rate!r} is not a number"
    if rate > 1:
        return f"rate {rate!r} is above 1 -- a fraction was expected, not a percent"
    if not (RATE_MIN <= rate <= RATE_MAX):
        return f"rate {rate!r} is outside the statutory band {RATE_MIN}..{RATE_MAX}"

    span = verdict.get("quoted_span")
    if not span:
        return "rate present with no quoted_span"
    text = _task_text(verdict.get("document_id", ""))
    if text is None:
        return "no task text on file to verify the quoted_span against"
    if span not in text:
        return "quoted_span does not appear in the document's text -- produced, not read"
    percent_digits = str(int(round(rate * 100)))
    if percent_digits not in span:
        return (
            f"quoted_span does not contain the rate's digits ({percent_digits}) -- "
            "any substring of the document would otherwise pass"
        )
    return None


def compute_results() -> list[dict[str, Any]]:
    """Apply the disagreement policy to every verdict, in code.

    Five outcomes, matching the approved policy exactly:

    - agree, new or published row              -> AGREE, eligible / retained
    - disagree, no published row yet           -> BLOCKED, does not enter
    - disagree, already-published row          -> FLAGGED, retained, never removed
    - parser silent, no published row, rate    -> ESCALATED, a new candidate
    - parser silent, ALREADY published, rate   -> PUBLISHED_SUPPORT_WITHDRAWN,
      the one a human must act on: this district's row in data/rates.csv now
      rests on parser support that no longer exists

    A verdict that fails `validate_verdict()` is treated exactly like a
    refusal: its structural defect means it was not read out of the document,
    so it carries no weight either way. A verdict `validate_verdict()` was
    never even able to check — no task file on record — is neither: it is
    `CANNOT_VERIFY`, checked first, before refusal is even asked about.
    """
    parser_rates = load_parser_rates()
    published = published_document_ids(RATES_CSV)
    results: list[dict[str, Any]] = []

    for verdict in load_verdicts():
        document_id = verdict.get("document_id", "")

        if verdict_unverifiable(verdict):
            results.append(
                {
                    "document_id": document_id,
                    "outcome": CANNOT_VERIFY,
                    "reason": (
                        f"no task file on record for {document_id} -- quoted_span could "
                        "not be checked against the document text, so this verdict was "
                        "never actually checked"
                    ),
                }
            )
            continue

        objection = validate_verdict(verdict)
        if verdict.get("refused") or objection:
            results.append(
                {
                    "document_id": document_id,
                    "outcome": REFUSED,
                    "reason": verdict.get("refusal_reason") or objection or "refused",
                }
            )
            continue

        model_rate = verdict["rate"]
        parser_rate = parser_rates.get(document_id)
        is_published = document_id in published

        if parser_rate is None:
            if is_published:
                outcome = PUBLISHED_SUPPORT_WITHDRAWN
                reason = (
                    f"{document_id} IS PUBLISHED in data/rates.csv, but the parser no "
                    f"longer confirms a rate for it (model read {model_rate}) -- a "
                    "human must check whether this district's row is still supported"
                )
            else:
                outcome = ESCALATED
                reason = f"parser has no confirmed rate for {document_id}; model read {model_rate}"
        elif model_rate == parser_rate:
            outcome = AGREE
            reason = f"model agrees with the parser: {model_rate}"
        elif is_published:
            outcome = FLAGGED
            reason = (
                f"model reads {model_rate}, published rate is {parser_rate} -- "
                "retained, not removed"
            )
        else:
            outcome = BLOCKED
            reason = f"model reads {model_rate}, parser reads {parser_rate} -- not published"

        results.append(
            {
                "document_id": document_id,
                "outcome": outcome,
                "model_rate": model_rate,
                "parser_rate": parser_rate,
                "reason": reason,
            }
        )

    return results


def _parser_rates_fingerprint(parser_rates: dict[str, float]) -> str:
    """A short digest of the parser rates a results file was computed against.

    Recorded alongside every write to `data/validation-results.json`, and
    recomputed on every `--report`, so a committed results file that has
    drifted from what `data/extracted-rates.json` currently holds is
    detectable rather than silently trusted.
    """
    payload = json.dumps(sorted(parser_rates.items()), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ingest(allow_shrink: bool = False) -> list[dict[str, Any]]:
    """Compute every verdict's outcome and write it to `data/validation-results.json`.

    Refuses in two situations, neither of which may overwrite a good results
    file with a worse one:

    - any verdict is `CANNOT_VERIFY` (`CannotVerify`) — the run cannot prove
      what it would be claiming, so it must not claim anything at all;
    - the write would drop document ids the existing file already covers
      (`WouldShrinkDataset`, the same guard `extract_rates.write_outputs`
      uses for the same reason) — a wholesale write over a partial run would
      silently erase every result outside that run, and a partial run is the
      likely shape of the very next call: re-ingest after fixing one verdict.
    """
    results = compute_results()

    unverifiable = [r for r in results if r["outcome"] == CANNOT_VERIFY]
    if unverifiable:
        raise CannotVerify(
            f"{len(unverifiable)} verdict(s) could not be verified -- no task file on "
            f"record (run `--emit` first). Refusing to write {RESULTS}: "
            f"{sorted(r['document_id'] for r in unverifiable)[:5]}"
        )

    if RESULTS.exists() and not allow_shrink:
        existing = json.loads(RESULTS.read_text(encoding="utf-8")).get("results", [])
        existing_ids = {r["document_id"] for r in existing}
        writing_ids = {r["document_id"] for r in results}
        missing = existing_ids - writing_ids
        if missing:
            raise WouldShrinkDataset(
                f"this run covers {len(writing_ids)} verdict(s) and the file holds "
                f"{len(existing_ids)}; {len(missing)} would be deleted, e.g. "
                f"{sorted(missing)[:3]}. Re-run over the full verdict set, or pass "
                f"--allow-shrink if the verdict set really did shrink."
            )

    RESULTS.write_text(
        json.dumps(
            {
                "results": results,
                "parser_rates_fingerprint": _parser_rates_fingerprint(load_parser_rates()),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return results


def report() -> str:
    """The counts a human needs before touching anything: verdicts, outcomes, gaps."""
    verdicts = load_verdicts()
    results = compute_results()
    counts = Counter(r["outcome"] for r in results)

    parser_rates = load_parser_rates()
    verdicted_ids = {v.get("document_id") for v in verdicts}
    no_verdict = sorted(set(parser_rates) - verdicted_ids)
    queue_ids = load_queue_ids()

    lines: list[str] = []

    if RESULTS.exists():
        committed = json.loads(RESULTS.read_text(encoding="utf-8"))
        committed_fingerprint = committed.get("parser_rates_fingerprint")
        current_fingerprint = _parser_rates_fingerprint(parser_rates)
        if committed_fingerprint is not None and committed_fingerprint != current_fingerprint:
            lines += [
                f"STALE: {RESULTS} was computed against a different set of parser "
                "rates than data/extracted-rates.json currently holds. The counts "
                "below are recomputed live and are correct; the COMMITTED FILE is "
                "not. Run `--ingest` to refresh it before anything downstream trusts it.",
                "",
            ]

    if counts[PUBLISHED_SUPPORT_WITHDRAWN]:
        lines += [
            f"!!! {counts[PUBLISHED_SUPPORT_WITHDRAWN]} PUBLISHED DISTRICT(S) HAVE HAD "
            "PARSER SUPPORT WITHDRAWN -- see published_support_withdrawn rows for the "
            "document ids. A human must check these rows in data/rates.csv.",
            "",
        ]

    lines += [
        f"verdicts total: {len(verdicts)}",
        f"agree: {counts[AGREE]}",
        f"disagree-new-blocked: {counts[BLOCKED]}",
        f"disagree-published-flagged: {counts[FLAGGED]}",
        f"escalated: {counts[ESCALATED]}",
        f"published-support-withdrawn: {counts[PUBLISHED_SUPPORT_WITHDRAWN]}",
        f"refused: {counts[REFUSED]}",
        f"cannot-verify (no task file on record): {counts[CANNOT_VERIFY]}",
        f"documents with no verdict yet: {len(no_verdict)}",
        f"could not be fetched on last --emit: {len(load_unavailable())}",
        "",
        f"coverage: verdicts are compared against the {len(parser_rates)} CONFIRMED "
        f"document(s) in data/extracted-rates.json by default. The extraction queue "
        f"holds {len(queue_ids)} further document(s) NOT covered unless `--emit "
        "--population queued` (or `all`) was used to reach them -- this report does "
        "not imply the queue has a second opinion on it.",
    ]

    return "\n".join(lines)


def merge_verdicts() -> list[dict[str, Any]]:
    """Concatenate `data/validation-verdicts.d/batch*.json` into the single verdict file.

    Until this existed, no committed code reproduced `data/validation-verdicts.json`
    from the batch files that are its real, human-reviewed source -- it was
    regenerable from nothing at all. Refuses on a document id appearing in more
    than one batch rather than silently keeping the last one: two verdicts for
    the same document disagreeing about which batch is authoritative is a real
    problem, not a merge conflict to paper over.
    """
    if not VERDICTS_DIR.exists():
        raise FileNotFoundError(f"{VERDICTS_DIR} does not exist")
    batches = sorted(VERDICTS_DIR.glob("batch*.json"))
    if not batches:
        raise FileNotFoundError(f"no batch*.json files in {VERDICTS_DIR}")

    merged: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for batch in batches:
        entries = json.loads(batch.read_text(encoding="utf-8"))
        for entry in entries:
            document_id = entry.get("document_id")
            if document_id in seen:
                raise ValueError(
                    f"{document_id!r} appears in both {seen[document_id]} and "
                    f"{batch.name} -- duplicate verdict, refusing to merge silently"
                )
            seen[document_id] = batch.name
            merged.append(entry)

    VERDICTS.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="The LLM validation gate over a blind reading.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit", action="store_true", help="write blind task files")
    mode.add_argument(
        "--merge-verdicts",
        action="store_true",
        help="concatenate data/validation-verdicts.d/batch*.json into validation-verdicts.json",
    )
    mode.add_argument("--ingest", action="store_true", help="apply the disagreement policy")
    mode.add_argument("--report", action="store_true", help="print the summary counts")
    parser.add_argument(
        "document_ids",
        nargs="*",
        help="with --emit: which documents to write tasks for; default is every "
        "document in --population",
    )
    parser.add_argument(
        "--population",
        choices=["confirmed", "queued", "all"],
        default="confirmed",
        help="with --emit and no explicit document_ids: which set to cover -- "
        "'confirmed' (default) is the parser's own rows; 'queued' reaches the "
        "extraction queue, including terminal regime refusals, where a second "
        "opinion is worth the most; 'all' is both",
    )
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="with --ingest: permit a write that drops document ids the existing "
        "results file already covers",
    )
    arguments = parser.parse_args()

    if arguments.emit:
        if arguments.document_ids:
            ids = arguments.document_ids
            population = "explicit"
        else:
            confirmed_ids = sorted(load_parser_rates())
            queued_ids = load_queue_ids()
            if arguments.population == "confirmed":
                ids = confirmed_ids
            elif arguments.population == "queued":
                ids = queued_ids
            else:
                ids = sorted(set(confirmed_ids) | set(queued_ids))
            population = arguments.population
            clear_tasks_dir()
        emitted, unavailable = emit_tasks(ids)
        print(f"population: {population} ({len(ids)} document id(s))")
        print(f"emitted {len(emitted)} task(s): {emitted}")
        print(f"UNAVAILABLE, no task written: {unavailable}")
        return 0

    if arguments.merge_verdicts:
        merged = merge_verdicts()
        print(f"merged {len(merged)} verdict(s) into {VERDICTS}")
        return 0

    if arguments.ingest:
        try:
            results = ingest(allow_shrink=arguments.allow_shrink)
        except (CannotVerify, WouldShrinkDataset) as error:
            print(f"REFUSED: {error}")
            return 1
        print(f"wrote {len(results)} result(s) to {RESULTS}")
        return 0

    print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
