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
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from extract_rates import BASE_URL, EXTRACTED, cached_document, pdf_text
from validate import RATES_CSV, REPO_ROOT

TASKS_DIR = REPO_ROOT / "data" / "validation-tasks"
VERDICTS = REPO_ROOT / "data" / "validation-verdicts.json"
RESULTS = REPO_ROOT / "data" / "validation-results.json"

# The complete set of fields a task file may ever carry. Anything else is a
# leak: it hands the reading agent information beyond the document itself.
TASK_FIELDS = frozenset({"document_id", "source_url", "text", "kazakh_text"})

# Outcomes of comparing a verdict against the deterministic result, applying
# "the model may veto and break a tie, and may never originate a rate."
AGREE = "agree"
BLOCKED = "blocked"
FLAGGED = "flagged"
ESCALATED = "escalated"
REFUSED = "refused"

DOCUMENT_ID_IN_URL = re.compile(r"/docs/([A-Za-z0-9]+)/?$")


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


def emit_tasks(document_ids: list[str]) -> tuple[list[str], list[str]]:
    """Write one blind task file per fetchable document.

    A document whose text cannot be obtained gets no task file at all — a
    missing document is a transport failure, not a reading, and a task with
    empty text would invite an answer from prior knowledge rather than from
    the page. Returns (emitted, unavailable), both sorted.
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

    return sorted(emitted), sorted(unavailable)


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


def validate_verdict(verdict: dict[str, Any]) -> str | None:
    """A structural objection to this verdict, or None if it is well-formed.

    Three checks, each guarding a specific way a verdict lies about having
    read the document:

    - a refused verdict needs nothing else — refusal is itself the answer;
    - a rate above 1 is the single most likely data-entry error here (storing
      3 for 3%), so it is rejected outright rather than silently rescaled;
    - a rate with no quoted span, or a quoted span that does not actually
      appear in the document's own text, means the model produced the span
      rather than read it — and that verdict must be refused, not trusted.
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

    span = verdict.get("quoted_span")
    if not span:
        return "rate present with no quoted_span"
    text = _task_text(verdict.get("document_id", ""))
    if text is None:
        return "no task text on file to verify the quoted_span against"
    if span not in text:
        return "quoted_span does not appear in the document's text -- produced, not read"
    return None


def compute_results() -> list[dict[str, Any]]:
    """Apply the disagreement policy to every verdict, in code.

    Four outcomes, matching the approved policy exactly:

    - agree, new or published row       -> AGREE, eligible / retained
    - disagree, no published row yet    -> BLOCKED, does not enter
    - disagree, already-published row   -> FLAGGED, retained, never removed
    - parser silent, verdict has a rate -> ESCALATED, never published

    A verdict that fails `validate_verdict()` is treated exactly like a
    refusal: its structural defect means it was not read out of the document,
    so it carries no weight either way.
    """
    parser_rates = load_parser_rates()
    published = published_document_ids(RATES_CSV)
    results: list[dict[str, Any]] = []

    for verdict in load_verdicts():
        document_id = verdict.get("document_id", "")
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


def ingest() -> list[dict[str, Any]]:
    """Compute every verdict's outcome and write it to `data/validation-results.json`."""
    results = compute_results()
    RESULTS.write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2) + "\n",
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

    return "\n".join(
        [
            f"verdicts total: {len(verdicts)}",
            f"agree: {counts[AGREE]}",
            f"disagree-new-blocked: {counts[BLOCKED]}",
            f"disagree-published-flagged: {counts[FLAGGED]}",
            f"escalated: {counts[ESCALATED]}",
            f"refused: {counts[REFUSED]}",
            f"documents with no verdict yet: {len(no_verdict)}",
        ]
    )


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="The LLM validation gate over a blind reading.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit", action="store_true", help="write blind task files")
    mode.add_argument("--ingest", action="store_true", help="apply the disagreement policy")
    mode.add_argument("--report", action="store_true", help="print the summary counts")
    parser.add_argument(
        "document_ids",
        nargs="*",
        help="with --emit: which documents to write tasks for; default is every "
        "document the deterministic readers confirmed",
    )
    arguments = parser.parse_args()

    if arguments.emit:
        ids = arguments.document_ids or sorted(load_parser_rates())
        emitted, unavailable = emit_tasks(ids)
        print(f"emitted {len(emitted)} task(s): {emitted}")
        print(f"UNAVAILABLE, no task written: {unavailable}")
        return 0

    if arguments.ingest:
        results = ingest()
        print(f"wrote {len(results)} result(s) to {RESULTS.relative_to(REPO_ROOT)}")
        return 0

    print(report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
