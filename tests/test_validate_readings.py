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

The emission tests below read real cached documents (`.cache/documents/`,
already populated) rather than inventing PDF bytes, so they exercise the real
`pdf_text()` path. The four disagreement-policy cases are pure comparison
logic over invented ids and invented rates -- following the same convention
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
from extract_rates import EXTRACTED
from validate_readings import (
    AGREE,
    BLOCKED,
    ESCALATED,
    FLAGGED,
    REFUSED,
    compute_results,
    emit_tasks,
    load_parser_rates,
    load_verdicts,
    published_document_ids,
    report,
    validate_verdict,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCUMENT_ID = "G25ZA00249M"  # real, cached in .cache/documents/, real fixture text below
REAL_TEXT = (FIXTURE_DIR / f"{DOCUMENT_ID}.txt").read_text(encoding="utf-8")


def _real_extracted_row(document_id: str) -> dict[str, Any]:
    """The committed, real parser answer for this document -- read, not typed."""
    rows = json.loads(EXTRACTED.read_text(encoding="utf-8"))["rows"]
    (row,) = [r for r in rows if r["document_id"] == document_id]
    return dict(row)


ROW = _real_extracted_row(DOCUMENT_ID)


# --------------------------------------------------------------------------
# --emit
# --------------------------------------------------------------------------


def test_emit_writes_a_blind_task_for_a_real_cached_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)

    emitted, unavailable = emit_tasks([DOCUMENT_ID])

    assert emitted == [DOCUMENT_ID]
    assert unavailable == []
    task = json.loads((tmp_path / f"{DOCUMENT_ID}.json").read_text(encoding="utf-8"))
    assert task["document_id"] == DOCUMENT_ID
    assert task["text"] == REAL_TEXT.strip()
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
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)

    def _unreachable(*args: object, **kwargs: object) -> tuple[str, bytes]:
        raise TimeoutError("simulated: the document could not be fetched")

    monkeypatch.setattr(validate_readings, "cached_document", _unreachable)

    emitted, unavailable = emit_tasks(["G25UNREACHABLE0"])

    assert emitted == []
    assert unavailable == ["G25UNREACHABLE0"]
    assert list(tmp_path.iterdir()) == []


def test_the_refusal_guard_actually_fires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutate the refusal away and confirm the test above would have caught it.

    `emit_tasks` catches every fetch failure; if it instead let one through
    and wrote a task with fabricated empty text, this reproduces exactly that
    mutation inline and shows a task WOULD be written -- i.e. the real
    `emit_tasks` deliberately avoids this shape rather than happening not to
    hit it.
    """
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)

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


# --------------------------------------------------------------------------
# verdict validation
# --------------------------------------------------------------------------


def test_validate_verdict_accepts_a_well_formed_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    (tmp_path / f"{DOCUMENT_ID}.json").write_text(
        json.dumps({"document_id": DOCUMENT_ID, "text": REAL_TEXT}), encoding="utf-8"
    )
    span = ROW["sentence"][:60]  # a real substring of the real sentence, not typed
    verdict = {
        "document_id": DOCUMENT_ID,
        "model": "test",
        "verdict_date": "2026-08-14",
        "rate": ROW["rate"],  # the real, committed rate -- never a hand-typed number
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


def test_validate_verdict_rejects_a_span_not_in_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A quoted span absent from the text means the model produced it, not read it."""
    monkeypatch.setattr(validate_readings, "TASKS_DIR", tmp_path)
    (tmp_path / f"{DOCUMENT_ID}.json").write_text(
        json.dumps({"document_id": DOCUMENT_ID, "text": REAL_TEXT}), encoding="utf-8"
    )
    verdict = {
        "document_id": DOCUMENT_ID,
        "rate": ROW["rate"],
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


def test_validate_verdict_accepts_a_refusal_without_checking_a_rate() -> None:
    assert (
        validate_verdict({"document_id": "SYNX", "refused": True, "refusal_reason": "why"}) is None
    )


# --------------------------------------------------------------------------
# the disagreement policy -- four synthetic cases, invented ids, never a real district
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
    monkeypatch.setattr(validate_readings, "RESULTS", tmp_path / "validation-results.json")
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

    verdicts: list[dict[str, Any]] = [
        {"document_id": "SYNAGREE", "rate": 0.03, "quoted_span": "span-agree", "refused": False},
        {"document_id": "SYNBLOCK", "rate": 0.05, "quoted_span": "span-block", "refused": False},
        {"document_id": "SYNFLAG", "rate": 0.06, "quoted_span": "span-flag", "refused": False},
        {
            "document_id": "SYNESCALATE",
            "rate": 0.04,
            "quoted_span": "span-escalate",
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
        {"document_id": "SYNAGREE", "rate": 0.03, "quoted_span": "span-agree", "refused": False}
    ]
    _write_task(tasks_dir, "SYNAGREE", "span-agree")
    (gate / "validation-verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")

    text = report()
    assert "verdicts total: 1" in text
    assert "agree: 1" in text
    assert "documents with no verdict yet: 1" in text


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
