"""The LLM validation gate: emission, verdict validation, and the disagreement policy.

`test_validation_leak.py` covers the one rule that decides whether this gate
is worth building at all -- that a task file cannot carry the parser's
answer. This file covers everything downstream of that: a document that
cannot be fetched gets no task and is not silently answered from prior
knowledge; a verdict that lies about having read the document (a rate stored
as a percent, a quoted span that is not actually in the text) is refused; and
comparing a verdict against the deterministic result applies the approved
policy -- the model may veto and may break a tie, and may never originate a
rate.

The emission tests below stub `cached_document` and `pdf_text` rather than
touching the real network or the real `.cache/` -- CI has neither, and a test
that only passes with a warm local cache is a test that only proves something
on this machine. `REAL_TEXT` (a fixture committed as text, matching
`tests/test_extraction.py`'s convention) still stands in for the genuine
document content. The disagreement-policy cases are pure comparison logic
over invented ids and invented rates -- following the same convention
`tests/test_pipeline.py` already uses for its own invented `VALID` row --
because that logic has nothing to do with any real district; it is exercised
with synthetic document ids that start with `SYN-` and never touch
`data/rates.csv` or any real document's rate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import validate_readings
from extract_rates import EXTRACTED, WouldShrinkDataset
from validate_readings import (
    AGREE,
    BLOCKED,
    CANNOT_VERIFY,
    ESCALATED,
    FLAGGED,
    PUBLISHED_SUPPORT_WITHDRAWN,
    REFUSED,
    CannotVerify,
    compute_results,
    emit_tasks,
    ingest,
    load_parser_rates,
    load_queue_ids,
    load_verdicts,
    merge_verdicts,
    published_document_ids,
    report,
    validate_verdict,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCUMENT_ID = "G25ZA00249M"  # real, has a real fixture text below
REAL_TEXT = (FIXTURE_DIR / f"{DOCUMENT_ID}.txt").read_text(encoding="utf-8")
REAL_KAZ_TEXT = (FIXTURE_DIR / f"{DOCUMENT_ID}.kaz.txt").read_text(encoding="utf-8")


def _real_extracted_row(document_id: str) -> dict[str, Any]:
    """The committed, real parser answer for this document -- read, not typed."""
    rows = json.loads(EXTRACTED.read_text(encoding="utf-8"))["rows"]
    (row,) = [r for r in rows if r["document_id"] == document_id]
    return dict(row)


@pytest.fixture
def real_row() -> dict[str, Any]:
    """The committed row for DOCUMENT_ID, looked up per-test rather than at import.

    A module-level `ROW = _real_extracted_row(DOCUMENT_ID)` would run this
    lookup at collection time: if DOCUMENT_ID ever left the confirmed set --
    which is exactly what just happened to eight other documents when the
    regime reader was added -- every test in this file would error at
    collection instead of the one test that actually needs the row failing on
    its own. A fixture defers the lookup to when a test asks for it.
    """
    return _real_extracted_row(DOCUMENT_ID)


def _stub_cached_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace `cached_document`/`pdf_text` so emission never touches a network.

    `.cache/documents/` is gitignored and absent on a fresh clone or CI, so a
    test that calls the real `cached_document` for a real id either hits the
    network or fails outright depending on what happens to be cached on this
    machine. This stub returns the real fixture text for the Russian file and
    the real Kazakh fixture for the Kazakh one, keyed on the `language`
    keyword `emit_tasks` actually passes -- so it still exercises the real
    `emit_tasks` control flow, just without the transport underneath it.
    """

    def _fake_cached_document(
        document_id: str, language: str = "rus", refresh: bool = False
    ) -> tuple[str, bytes]:
        return f"https://example.invalid/{document_id}.{language}.pdf", language.encode()

    def _fake_pdf_text(payload: bytes) -> str:
        return REAL_KAZ_TEXT.strip() if payload == b"kaz" else REAL_TEXT.strip()

    monkeypatch.setattr(validate_readings, "cached_document", _fake_cached_document)
    monkeypatch.setattr(validate_readings, "pdf_text", _fake_pdf_text)


# --------------------------------------------------------------------------
# --emit
# --------------------------------------------------------------------------


def test_emit_writes_a_blind_task_for_a_real_cached_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    monkeypatch.setattr(validate_readings, "UNAVAILABLE_LOG", tmp_path / "unavailable.json")
    _stub_cached_document(monkeypatch)

    emitted, unavailable = emit_tasks([DOCUMENT_ID])

    assert emitted == [DOCUMENT_ID]
    assert unavailable == []
    task = json.loads((tmp_path / f"{DOCUMENT_ID}.json").read_text(encoding="utf-8"))
    assert task["document_id"] == DOCUMENT_ID
    assert task["text"] == REAL_TEXT.strip()
    assert task["kazakh_text"] == REAL_KAZ_TEXT.strip()
    assert set(task) == {"document_id", "source_url", "text", "kazakh_text"}


def test_emit_refuses_rather_than_answer_when_the_document_cannot_be_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fetch failure is a transport failure, not a reading -- no task, no answer.

    A task with empty or absent text would invite the reading agent to answer
    from prior knowledge, which is worse than the parser finding nothing, so
    the correct behaviour is silence: no file written, the id reported back
    as unavailable.
    """
    tasks_dir = tmp_path / "tasks"
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(validate_readings, "UNAVAILABLE_LOG", tmp_path / "unavailable.json")

    def _unreachable(*args: object, **kwargs: object) -> tuple[str, bytes]:
        raise TimeoutError("simulated: the document could not be fetched")

    monkeypatch.setattr(validate_readings, "cached_document", _unreachable)

    emitted, unavailable = emit_tasks(["G25UNREACHABLE0"])

    assert emitted == []
    assert unavailable == ["G25UNREACHABLE0"]
    assert list(tasks_dir.glob("*.json")) == []


def test_emit_refuses_rather_than_answer_when_the_extracted_text_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other way a document yields nothing: it fetches fine, but extracts blank.

    Distinct from the fetch-failure case above and guarded by a different
    line (`if not text.strip(): unavailable.append(...)`). Exercised against
    the REAL `emit_tasks`, not a mutant copy standing in for it -- a mutant
    proves the shape of a bug, never that the real function avoids it.
    """
    tasks_dir = tmp_path / "tasks"
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(validate_readings, "UNAVAILABLE_LOG", tmp_path / "unavailable.json")
    monkeypatch.setattr(
        validate_readings, "cached_document", lambda *a, **k: ("https://example.invalid", b"x")
    )
    monkeypatch.setattr(validate_readings, "pdf_text", lambda payload: "   \n\t  ")

    emitted, unavailable = emit_tasks(["G25EMPTYTEXT0"])

    assert emitted == []
    assert unavailable == ["G25EMPTYTEXT0"]
    assert list(tasks_dir.glob("*.json")) == []


def test_the_refusal_guard_actually_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutate the refusal away and confirm the test above would have caught it.

    `emit_tasks` catches every fetch failure; if it instead let one through
    and wrote a task with fabricated empty text, this reproduces exactly that
    mutation inline and shows a task WOULD be written -- i.e. the real
    `emit_tasks` deliberately avoids this shape rather than happening not to
    hit it.
    """
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    monkeypatch.setattr(validate_readings, "UNAVAILABLE_LOG", tmp_path / "unavailable.json")

    def _mutated_emit_that_answers_anyway(document_ids: list[str]) -> tuple[list[str], list[str]]:
        # The mutation: on failure, write a task with empty text instead of
        # refusing. This is the shape the real emit_tasks must never take.
        for document_id in document_ids:
            (tmp_path / f"{document_id}.json").write_text(
                json.dumps({"document_id": document_id, "text": ""}), encoding="utf-8"
            )
        return document_ids, []

    emitted, unavailable = _mutated_emit_that_answers_anyway(["G25UNREACHABLE0"])
    assert emitted == ["G25UNREACHABLE0"]  # the mutated version wrongly claims success
    assert (tmp_path / "G25UNREACHABLE0.json").exists()  # and wrongly wrote a task

    # The real function, against the same failure, does neither.
    (tmp_path / "G25UNREACHABLE0.json").unlink()

    def _unreachable(*args: object, **kwargs: object) -> tuple[str, bytes]:
        raise TimeoutError("simulated")

    monkeypatch.setattr(validate_readings, "cached_document", _unreachable)
    real_emitted, real_unavailable = emit_tasks(["G25UNREACHABLE0"])
    assert real_emitted == []
    assert real_unavailable == ["G25UNREACHABLE0"]
    assert not (tmp_path / "G25UNREACHABLE0.json").exists()


def test_load_queue_ids_reads_document_ids_from_the_extraction_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_path = tmp_path / "extraction-queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "pending": [
                    {"document_id": "SYNQ1", "outcome": "conflict"},
                    {"document_id": "SYNQ2", "outcome": "unparsed"},
                    {"document_id": "SYNQ1", "outcome": "conflict"},  # duplicate, must not double
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(validate_readings, "QUEUE", queue_path)
    assert load_queue_ids() == ["SYNQ1", "SYNQ2"]


def test_load_queue_ids_is_empty_when_the_queue_file_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_readings, "QUEUE", tmp_path / "does-not-exist.json")
    assert load_queue_ids() == []


def test_clear_tasks_dir_removes_every_existing_task_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale task from a document that left the population must not survive a full run."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "STALE.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tasks_dir)

    validate_readings.clear_tasks_dir()

    assert list(tasks_dir.glob("*.json")) == []


def test_clear_tasks_dir_tolerates_a_missing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path / "does-not-exist")
    validate_readings.clear_tasks_dir()  # must not raise


# --------------------------------------------------------------------------
# verdict validation
# --------------------------------------------------------------------------


def test_validate_verdict_accepts_a_well_formed_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_row: dict[str, Any]
) -> None:
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    (tmp_path / f"{DOCUMENT_ID}.json").write_text(
        json.dumps({"document_id": DOCUMENT_ID, "text": REAL_TEXT}), encoding="utf-8"
    )
    # The last 120 characters of the real sentence -- long enough to carry the
    # rate's own digit ("3"), not just any substring of the document.
    span = real_row["sentence"][-120:]
    assert "3" in span  # the fixture assumption this test relies on, stated plainly
    verdict = {
        "document_id": DOCUMENT_ID,
        "model": "test",
        "verdict_date": "2026-08-14",
        "rate": real_row["rate"],  # the real, committed rate -- never a hand-typed number
        "quoted_span": span,
        "refused": False,
    }
    assert validate_verdict(verdict) is None


def test_validate_verdict_rejects_a_rate_stored_as_a_percent() -> None:
    """The single most likely data-entry error: 3 meaning 3%, not 0.03."""
    verdict = {"document_id": "SYNX", "rate": 3.0, "quoted_span": "irrelevant", "refused": False}
    objection = validate_verdict(verdict)
    assert objection is not None
    assert "above 1" in objection


@pytest.mark.parametrize("bad_rate", [0.0, -0.03, 1.0, 0.5])
def test_validate_verdict_rejects_a_rate_outside_the_statutory_band(bad_rate: float) -> None:
    """0.0, a negative, and 1.0 all pass a bare `rate > 1` clamp. None may pass here.

    `0.5` is included because it is a plausible-looking fraction (unlike 3.0)
    that a percent-only clamp would also have waved through -- the failure
    mode isn't limited to the one data-entry mistake the old check named.
    """
    verdict = {"document_id": "SYNX", "rate": bad_rate, "quoted_span": "x", "refused": False}
    objection = validate_verdict(verdict)
    assert objection is not None
    assert "statutory band" in objection


def test_the_band_check_actually_fires() -> None:
    """Mutate the band check back to the old `rate > 1` clamp and watch it pass 0.0."""

    def _mutated_validate_with_old_clamp(verdict: dict) -> str | None:
        if verdict.get("refused"):
            return None
        rate = verdict.get("rate")
        if rate is None:
            return "no rate"
        if rate > 1:
            return "above 1"
        return None  # the mutation: nothing rejects 0.0, a negative, or 1.0

    fabricated = {"document_id": "SYNX", "rate": 0.0, "quoted_span": "x", "refused": False}
    assert _mutated_validate_with_old_clamp(fabricated) is None  # wrongly accepted
    assert validate_verdict(fabricated) is not None  # the real function refuses it


def test_validate_verdict_rejects_a_span_not_in_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_row: dict[str, Any]
) -> None:
    """A quoted span absent from the text means the model produced it, not read it."""
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    (tmp_path / f"{DOCUMENT_ID}.json").write_text(
        json.dumps({"document_id": DOCUMENT_ID, "text": REAL_TEXT}), encoding="utf-8"
    )
    verdict = {
        "document_id": DOCUMENT_ID,
        "rate": real_row["rate"],
        "quoted_span": "a sentence that does not appear anywhere in this document",
        "refused": False,
    }
    objection = validate_verdict(verdict)
    assert objection is not None
    assert "produced, not read" in objection


def test_the_span_check_actually_fires() -> None:
    """Mutate the span check away and confirm it would have let the bad verdict through."""

    def _mutated_validate_without_span_check(verdict: dict) -> str | None:
        if verdict.get("refused"):
            return None
        rate = verdict.get("rate")
        if rate is None or rate > 1:
            return "bad rate"
        return None  # the mutation: never checks quoted_span against the text at all

    fabricated = {
        "document_id": DOCUMENT_ID,
        "rate": 0.03,
        "quoted_span": "a sentence that does not appear anywhere in this document",
        "refused": False,
    }
    assert _mutated_validate_without_span_check(fabricated) is None  # wrongly accepted
    # The real function does not.
    assert validate_verdict(fabricated) is not None


def test_validate_verdict_rejects_a_span_that_appears_but_carries_no_digit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_row: dict[str, Any]
) -> None:
    """A span-in-text check alone passes any substring: a header, a district's name.

    Real district name from the real document, genuinely present in the
    text, and genuinely says nothing about the rate.
    """
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    (tmp_path / f"{DOCUMENT_ID}.json").write_text(
        json.dumps({"document_id": DOCUMENT_ID, "text": REAL_TEXT}), encoding="utf-8"
    )
    verdict = {
        "document_id": DOCUMENT_ID,
        "rate": real_row["rate"],
        "quoted_span": "Уральск",
        "refused": False,
    }
    assert "Уральск" in REAL_TEXT  # the assumption: this really is a substring of the text
    objection = validate_verdict(verdict)
    assert objection is not None
    assert "digits" in objection


def test_the_digit_check_actually_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutate the digit check away and confirm a digit-free span would pass."""
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    (tmp_path / f"{DOCUMENT_ID}.json").write_text(
        json.dumps({"document_id": DOCUMENT_ID, "text": REAL_TEXT}), encoding="utf-8"
    )

    def _mutated_validate_without_digit_check(verdict: dict) -> str | None:
        if verdict.get("refused"):
            return None
        rate = verdict.get("rate")
        if rate is None:
            return "no rate"
        span = verdict.get("quoted_span")
        text = REAL_TEXT
        if not span or span not in text:
            return "span check failed"
        return None  # the mutation: never checks the span for the rate's own digits

    fabricated = {
        "document_id": DOCUMENT_ID,
        "rate": 0.03,
        "quoted_span": "Уральск",
        "refused": False,
    }
    assert _mutated_validate_without_digit_check(fabricated) is None  # wrongly accepted
    assert validate_verdict(fabricated) is not None  # the real function refuses it


def test_validate_verdict_reports_no_task_text_when_the_task_file_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    verdict = {"document_id": "SYNNOTASK", "rate": 0.03, "quoted_span": "x", "refused": False}
    objection = validate_verdict(verdict)
    assert objection == "no task text on file to verify the quoted_span against"


def test_validate_verdict_accepts_a_refusal_without_checking_a_rate() -> None:
    assert (
        validate_verdict({"document_id": "SYNX", "refused": True, "refusal_reason": "why"}) is None
    )


# --------------------------------------------------------------------------
# the disagreement policy -- synthetic cases, invented ids, never a real district
# --------------------------------------------------------------------------


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every file the gate reads and writes at an isolated tmp_path."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tasks_dir)
    monkeypatch.setattr(validate_readings, "EXTRACTED", tmp_path / "extracted-rates.json")
    monkeypatch.setattr(validate_readings, "RATES_CSV", tmp_path / "rates.csv")
    monkeypatch.setattr(validate_readings, "VERDICTS", tmp_path / "validation-verdicts.json")
    monkeypatch.setattr(validate_readings, "VERDICTS_DIR", tmp_path / "validation-verdicts.d")
    monkeypatch.setattr(validate_readings, "RESULTS", tmp_path / "validation-results.json")
    monkeypatch.setattr(validate_readings, "UNAVAILABLE_LOG", tmp_path / "unavailable.json")
    monkeypatch.setattr(validate_readings, "QUEUE", tmp_path / "extraction-queue.json")
    return tmp_path


def _write_task(tasks_dir: Path, document_id: str, span: str) -> None:
    tasks_dir.mkdir(exist_ok=True)
    (tasks_dir / f"{document_id}.json").write_text(
        json.dumps({"document_id": document_id, "text": f"decision text mentioning {span} here"}),
        encoding="utf-8",
    )


def test_four_synthetic_disagreement_cases(gate: Path) -> None:
    tasks_dir = gate / "tasks"

    # The parser's own confirmed rows -- invented ids, never a real district.
    (gate / "extracted-rates.json").write_text(
        json.dumps(
            {
                "extraction_method": "deterministic-readers",
                "rows": [
                    {"document_id": "SYNAGREE", "rate": 0.03},
                    {"document_id": "SYNBLOCK", "rate": 0.04},
                    {"document_id": "SYNFLAG", "rate": 0.05},
                    # SYNESCALATE is deliberately absent: the parser is silent on it.
                ],
            }
        ),
        encoding="utf-8",
    )

    # SYNFLAG is already published; the other three are not.
    (gate / "rates.csv").write_text(
        "kato,source_url\n101000000,https://adilet.zan.kz/rus/docs/SYNFLAG\n",
        encoding="utf-8",
    )

    # Each span carries the digits of the RATE THE VERDICT REPORTS, per the
    # "quoted_span must contain the rate's own digits" rule.
    verdicts: list[dict[str, Any]] = [
        {"document_id": "SYNAGREE", "rate": 0.03, "quoted_span": "span-agree-3", "refused": False},
        {"document_id": "SYNBLOCK", "rate": 0.05, "quoted_span": "span-block-5", "refused": False},
        {"document_id": "SYNFLAG", "rate": 0.06, "quoted_span": "span-flag-6", "refused": False},
        {
            "document_id": "SYNESCALATE",
            "rate": 0.04,
            "quoted_span": "span-escalate-4",
            "refused": False,
        },
    ]
    for verdict in verdicts:
        _write_task(tasks_dir, str(verdict["document_id"]), str(verdict["quoted_span"]))
    (gate / "validation-verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")

    results = {r["document_id"]: r for r in compute_results()}

    assert results["SYNAGREE"]["outcome"] == AGREE
    assert results["SYNBLOCK"]["outcome"] == BLOCKED
    assert results["SYNFLAG"]["outcome"] == FLAGGED
    assert results["SYNESCALATE"]["outcome"] == ESCALATED


def test_a_refused_verdict_never_reaches_the_comparison(gate: Path) -> None:
    tasks_dir = gate / "tasks"
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNREFUSED", "rate": 0.03}]}), encoding="utf-8"
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [
                {
                    "document_id": "SYNREFUSED",
                    "rate": None,
                    "refused": True,
                    "refusal_reason": "no document text",
                }
            ]
        ),
        encoding="utf-8",
    )
    tasks_dir.mkdir(exist_ok=True)

    results = compute_results()
    assert results[0]["outcome"] == REFUSED


def test_published_support_withdrawn_when_the_parser_no_longer_confirms_a_published_row(
    gate: Path,
) -> None:
    """The row a human MUST act on: published in data/rates.csv, parser now silent.

    Exactly the shape found live: eight regime-refused documents that were
    already published (their parser support existed before the regime reader
    was added) now have the parser silent on them, and the model still reads
    a rate. That must not read as "a new candidate the parser missed" -- it
    reads as "this published row's support was withdrawn".
    """
    tasks_dir = gate / "tasks"
    # SYNWITHDRAWN is NOT in extracted-rates.json at all -- parser silent.
    (gate / "extracted-rates.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    (gate / "rates.csv").write_text(
        "kato,source_url\n101000000,https://adilet.zan.kz/rus/docs/SYNWITHDRAWN\n",
        encoding="utf-8",
    )
    _write_task(tasks_dir, "SYNWITHDRAWN", "span-2")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [
                {
                    "document_id": "SYNWITHDRAWN",
                    "rate": 0.02,
                    "quoted_span": "span-2",
                    "refused": False,
                }
            ]
        ),
        encoding="utf-8",
    )

    results = {r["document_id"]: r for r in compute_results()}
    assert results["SYNWITHDRAWN"]["outcome"] == PUBLISHED_SUPPORT_WITHDRAWN
    assert "PUBLISHED" in results["SYNWITHDRAWN"]["reason"]


def test_escalated_still_applies_when_the_parser_is_silent_and_nothing_is_published(
    gate: Path,
) -> None:
    """The other half of the split: parser silent, but no row exists to withdraw."""
    tasks_dir = gate / "tasks"
    (gate / "extracted-rates.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    _write_task(tasks_dir, "SYNNEW", "span-2")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNNEW", "rate": 0.02, "quoted_span": "span-2", "refused": False}]
        ),
        encoding="utf-8",
    )

    results = {r["document_id"]: r for r in compute_results()}
    assert results["SYNNEW"]["outcome"] == ESCALATED


def test_a_verdict_with_no_task_file_is_cannot_verify_not_refused(gate: Path) -> None:
    """No task file on record is a pipeline gap, and must never look like a refusal.

    `refused` counts a model's decisions; a document that could not even be
    checked was never given a chance to reach one.
    """
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNCANNOT", "rate": 0.03}]}), encoding="utf-8"
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    # No task file written for SYNCANNOT at all.
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNCANNOT", "rate": 0.03, "quoted_span": "span-3", "refused": False}]
        ),
        encoding="utf-8",
    )

    results = {r["document_id"]: r for r in compute_results()}
    assert results["SYNCANNOT"]["outcome"] == CANNOT_VERIFY


def test_ingest_refuses_to_write_when_any_verdict_cannot_be_verified(gate: Path) -> None:
    """`ingest()` must not overwrite good results with a file lying about coverage.

    Every verdict landing in `cannot_verify` (e.g. `data/validation-tasks/`
    absent on a fresh clone) would otherwise silently fold into the same
    write path that reports "wrote N result(s)" and exits 0 -- a clean bill
    of health for verdicts nobody actually checked.
    """
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNCANNOT", "rate": 0.03}]}), encoding="utf-8"
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNCANNOT", "rate": 0.03, "quoted_span": "span-3", "refused": False}]
        ),
        encoding="utf-8",
    )

    with pytest.raises(CannotVerify):
        ingest()
    assert not (gate / "validation-results.json").exists()


def test_the_cannot_verify_guard_actually_fires(gate: Path) -> None:
    """Mutate `ingest` to skip the CANNOT_VERIFY check and watch it write anyway."""
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNCANNOT", "rate": 0.03}]}), encoding="utf-8"
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNCANNOT", "rate": 0.03, "quoted_span": "span-3", "refused": False}]
        ),
        encoding="utf-8",
    )

    def _mutated_ingest_that_writes_anyway() -> None:
        results = compute_results()  # skips the CANNOT_VERIFY check entirely
        (gate / "validation-results.json").write_text(json.dumps({"results": results}))

    _mutated_ingest_that_writes_anyway()
    assert (gate / "validation-results.json").exists()  # wrongly wrote
    (gate / "validation-results.json").unlink()

    with pytest.raises(CannotVerify):
        ingest()  # the real function refuses
    assert not (gate / "validation-results.json").exists()


def test_ingest_refuses_a_write_that_would_shrink_the_results_file(gate: Path) -> None:
    """A partial re-ingest must not silently erase every result outside its run.

    Same reasoning, and the same exception class, as
    `extract_rates.write_outputs`'s `WouldShrinkDataset` guard.
    """
    (gate / "extracted-rates.json").write_text(
        json.dumps(
            {"rows": [{"document_id": "SYNA", "rate": 0.03}, {"document_id": "SYNB", "rate": 0.03}]}
        ),
        encoding="utf-8",
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    tasks_dir = gate / "tasks"
    _write_task(tasks_dir, "SYNA", "span-a-3")
    _write_task(tasks_dir, "SYNB", "span-b-3")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [
                {"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False},
                {"document_id": "SYNB", "rate": 0.03, "quoted_span": "span-b-3", "refused": False},
            ]
        ),
        encoding="utf-8",
    )
    ingest()  # first, full write: two results committed

    # Now a "partial" verdict file arrives, covering only SYNA.
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False}]
        ),
        encoding="utf-8",
    )
    with pytest.raises(WouldShrinkDataset):
        ingest()

    # --allow-shrink is the deliberate override.
    ingest(allow_shrink=True)
    written = json.loads((gate / "validation-results.json").read_text(encoding="utf-8"))
    assert len(written["results"]) == 1


def test_the_shrink_guard_actually_fires(gate: Path) -> None:
    """Mutate the shrink check away and watch a partial re-ingest silently erase."""
    (gate / "extracted-rates.json").write_text(
        json.dumps(
            {"rows": [{"document_id": "SYNA", "rate": 0.03}, {"document_id": "SYNB", "rate": 0.03}]}
        ),
        encoding="utf-8",
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    tasks_dir = gate / "tasks"
    _write_task(tasks_dir, "SYNA", "span-a-3")
    _write_task(tasks_dir, "SYNB", "span-b-3")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [
                {"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False},
                {"document_id": "SYNB", "rate": 0.03, "quoted_span": "span-b-3", "refused": False},
            ]
        ),
        encoding="utf-8",
    )
    ingest()

    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False}]
        ),
        encoding="utf-8",
    )

    def _mutated_ingest_that_shrinks_silently() -> None:
        results = compute_results()  # no comparison against the existing file at all
        (gate / "validation-results.json").write_text(json.dumps({"results": results}))

    _mutated_ingest_that_shrinks_silently()
    mutated = json.loads((gate / "validation-results.json").read_text(encoding="utf-8"))
    assert len(mutated["results"]) == 1  # SYNB silently gone

    # Restore, then confirm the real function refuses instead.
    (gate / "extracted-rates.json").write_text(
        json.dumps(
            {"rows": [{"document_id": "SYNA", "rate": 0.03}, {"document_id": "SYNB", "rate": 0.03}]}
        ),
        encoding="utf-8",
    )
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [
                {"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False},
                {"document_id": "SYNB", "rate": 0.03, "quoted_span": "span-b-3", "refused": False},
            ]
        ),
        encoding="utf-8",
    )
    ingest()
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False}]
        ),
        encoding="utf-8",
    )
    with pytest.raises(WouldShrinkDataset):
        ingest()


def test_report_flags_a_stale_committed_results_file(gate: Path) -> None:
    """`--report` must say the committed file is stale, not print stale counts as fact."""
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNA", "rate": 0.03}]}), encoding="utf-8"
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    tasks_dir = gate / "tasks"
    _write_task(tasks_dir, "SYNA", "span-a-3")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False}]
        ),
        encoding="utf-8",
    )
    ingest()  # committed against SYNA at 0.03

    # The parser rates on disk change WITHOUT a re-ingest -- exactly what
    # happened for real: extracted-rates.json changed, validation-results.json
    # did not follow.
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNA", "rate": 0.05}]}), encoding="utf-8"
    )

    text = report()
    assert "STALE" in text


def test_report_says_nothing_is_stale_right_after_a_matching_ingest(gate: Path) -> None:
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNA", "rate": 0.03}]}), encoding="utf-8"
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    tasks_dir = gate / "tasks"
    _write_task(tasks_dir, "SYNA", "span-a-3")
    (gate / "validation-verdicts.json").write_text(
        json.dumps(
            [{"document_id": "SYNA", "rate": 0.03, "quoted_span": "span-a-3", "refused": False}]
        ),
        encoding="utf-8",
    )
    ingest()

    assert "STALE" not in report()


def test_report_counts_match_the_disagreement_policy(gate: Path) -> None:
    tasks_dir = gate / "tasks"
    (gate / "extracted-rates.json").write_text(
        json.dumps(
            {
                "rows": [
                    {"document_id": "SYNAGREE", "rate": 0.03},
                    {"document_id": "SYNNOVERDICT", "rate": 0.03},
                ]
            }
        ),
        encoding="utf-8",
    )
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    verdicts = [
        {"document_id": "SYNAGREE", "rate": 0.03, "quoted_span": "span-agree-3", "refused": False}
    ]
    _write_task(tasks_dir, "SYNAGREE", "span-agree-3")
    (gate / "validation-verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")

    text = report()
    assert "verdicts total: 1" in text
    assert "agree: 1" in text
    assert "documents with no verdict yet: 1" in text


def test_report_states_the_population_it_covers(gate: Path) -> None:
    (gate / "extracted-rates.json").write_text(json.dumps({"rows": []}), encoding="utf-8")
    (gate / "rates.csv").write_text("kato,source_url\n", encoding="utf-8")
    (gate / "extraction-queue.json").write_text(
        json.dumps({"pending": [{"document_id": "SYNQUEUED", "outcome": "conflict"}]}),
        encoding="utf-8",
    )
    text = report()
    assert "coverage" in text
    assert "1 further document" in text


def test_published_document_ids_reads_from_source_url(tmp_path: Path) -> None:
    path = tmp_path / "rates.csv"
    path.write_text(
        "kato,source_url\n101000000,https://adilet.zan.kz/rus/docs/G25AAA00001M\n",
        encoding="utf-8",
    )
    assert published_document_ids(path) == {"G25AAA00001M"}


def test_load_parser_rates_reads_only_confirmed_rows(gate: Path) -> None:
    (gate / "extracted-rates.json").write_text(
        json.dumps({"rows": [{"document_id": "SYNX", "rate": 0.02}]}), encoding="utf-8"
    )
    assert load_parser_rates() == {"SYNX": 0.02}


def test_load_verdicts_returns_empty_list_when_no_file_exists(gate: Path) -> None:
    assert load_verdicts() == []


# --------------------------------------------------------------------------
# merge_verdicts
# --------------------------------------------------------------------------


def test_merge_verdicts_concatenates_every_batch_file(gate: Path) -> None:
    batches_dir = gate / "validation-verdicts.d"
    batches_dir.mkdir()
    (batches_dir / "batch1.json").write_text(
        json.dumps([{"document_id": "SYNA", "rate": 0.03}]), encoding="utf-8"
    )
    (batches_dir / "batch2.json").write_text(
        json.dumps([{"document_id": "SYNB", "rate": 0.04}]), encoding="utf-8"
    )
    merged = merge_verdicts()
    assert {v["document_id"] for v in merged} == {"SYNA", "SYNB"}
    written = json.loads((gate / "validation-verdicts.json").read_text(encoding="utf-8"))
    assert {v["document_id"] for v in written} == {"SYNA", "SYNB"}


def test_merge_verdicts_refuses_a_document_id_repeated_across_batches(gate: Path) -> None:
    batches_dir = gate / "validation-verdicts.d"
    batches_dir.mkdir()
    (batches_dir / "batch1.json").write_text(
        json.dumps([{"document_id": "SYNDUP", "rate": 0.03}]), encoding="utf-8"
    )
    (batches_dir / "batch2.json").write_text(
        json.dumps([{"document_id": "SYNDUP", "rate": 0.05}]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="SYNDUP"):
        merge_verdicts()


def test_the_duplicate_verdict_guard_actually_fires(gate: Path) -> None:
    """Mutate the merge to overwrite silently and watch the second rate win unremarked."""
    batches_dir = gate / "validation-verdicts.d"
    batches_dir.mkdir()
    (batches_dir / "batch1.json").write_text(
        json.dumps([{"document_id": "SYNDUP", "rate": 0.03}]), encoding="utf-8"
    )
    (batches_dir / "batch2.json").write_text(
        json.dumps([{"document_id": "SYNDUP", "rate": 0.05}]), encoding="utf-8"
    )

    def _mutated_merge_that_overwrites_silently() -> list[dict[str, Any]]:
        merged_by_id: dict[str, dict[str, Any]] = {}
        for batch in sorted(batches_dir.glob("batch*.json")):
            for entry in json.loads(batch.read_text(encoding="utf-8")):
                merged_by_id[entry["document_id"]] = entry  # last batch silently wins
        return list(merged_by_id.values())

    mutated = _mutated_merge_that_overwrites_silently()
    assert mutated[0]["rate"] == 0.05  # wrongly resolved with no error raised

    with pytest.raises(ValueError, match="SYNDUP"):
        merge_verdicts()  # the real function refuses instead
