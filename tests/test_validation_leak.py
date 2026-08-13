"""The blinding leak test -- written before the emitter it tests.

If a task file handed to a reading agent contains the parser's answer,
agreement stops being evidence: a model shown a number and asked to confirm it
agrees, because agreement is the plausible continuation. Everything else in
the validation gate is negotiable; this is not.

`_naive_emit` below is the design this test exists to forbid: it dumps the
whole extracted row -- rate, readings, sentence, decision_ref, all of it --
into the task "for context", which is exactly what a first draft of this gate
would do. `test_naive_emitter_leaks_the_parser_rate` proves the leak check
below actually catches that shape, run against a real committed row rather
than an invented one. `test_real_emitter_does_not_leak` then runs the same
check against `task_fields()` in `scripts/validate_readings.py` and it passes.

Development order, for the record: this file was written and run against
`_naive_emit` first. It failed -- `test_naive_emitter_leaks_the_parser_rate`
passed (correctly reporting a leak) but a mirrored assertion against
`_naive_emit` standing in for the real emitter failed, which is what "red"
looks like here. `task_fields` was then written to close it.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from extract_rates import EXTRACTED
from validate_readings import TASK_FIELDS, task_fields

FIXTURE_DIR = Path(__file__).parent / "fixtures"
DOCUMENT_ID = "G25ZA00249M"
REAL_TEXT = (FIXTURE_DIR / f"{DOCUMENT_ID}.txt").read_text(encoding="utf-8")


def _real_extracted_row(document_id: str) -> dict[str, Any]:
    """The committed, real parser answer for this document -- read, not typed.

    Using the actual `data/extracted-rates.json` row (rather than a rate typed
    into this file) means the leak check below is exercised against the real
    shape a naive emitter would have dumped in, including fields no invented
    fixture would think to include.
    """
    rows = json.loads(EXTRACTED.read_text(encoding="utf-8"))["rows"]
    (row,) = [r for r in rows if r["document_id"] == document_id]
    return dict(row)


@pytest.fixture
def real_row() -> dict[str, Any]:
    """The committed row for DOCUMENT_ID, looked up per-test rather than at import.

    A module-level `ROW = _real_extracted_row(DOCUMENT_ID)` runs at collection
    time: if DOCUMENT_ID ever left the confirmed set -- exactly what just
    happened to eight other documents when the regime reader was added --
    collecting this file would raise and every test in it would error, not
    just the one that needed the row. A fixture defers the lookup.
    """
    return _real_extracted_row(DOCUMENT_ID)


def _naive_emit(document_id: str, text: str, row: dict[str, Any]) -> dict[str, Any]:
    """The design this whole test file exists to forbid.

    A first draft of a blind-task emitter, written the way it is easy to
    write by accident: fetch the document, then hand the reading agent the
    parser's own row alongside it "so it has context". This is the version
    that must fail the leak check.
    """
    return {"document_id": document_id, "text": text, **row}


def _leaked_keys(task: dict[str, Any]) -> list[str]:
    """Which keys in `task` are not on the allowed, blind list."""
    return sorted(set(task) - TASK_FIELDS)


def test_naive_emitter_leaks_the_parser_rate(real_row: dict[str, Any]) -> None:
    """Proves the leak check itself works, before trusting it against anything real."""
    naive_task = _naive_emit(DOCUMENT_ID, REAL_TEXT, real_row)
    leaked = _leaked_keys(naive_task)

    assert "rate" in leaked, "the naive emitter's leak went undetected -- the check is broken"
    assert "readings" in leaked
    assert "sentence" in leaked
    assert naive_task["rate"] == real_row["rate"]


def test_real_emitter_does_not_leak() -> None:
    """`task_fields()` given the real document and the real row leaks nothing.

    The parser's row is read here only to prove it existed and was not passed
    through -- `task_fields()` itself has no parameter that could carry it.
    """
    real_task = task_fields(
        document_id=DOCUMENT_ID,
        source_url=f"https://adilet.zan.kz/rus/docs/{DOCUMENT_ID}",
        text=REAL_TEXT,
        kazakh_text=None,
    )

    assert _leaked_keys(real_task) == []
    assert real_task["document_id"] == DOCUMENT_ID
    assert real_task["text"] == REAL_TEXT
    # The parser's rate is nowhere in the assembled task other than as a
    # substring the document's own text was always going to contain
    # ("3 (три) процента" is in the decision itself, not injected by us).
    assert "rate" not in real_task
    assert "readings" not in real_task
    assert "sentence" not in real_task
    assert "decision_ref" not in real_task


def test_task_fields_has_no_parameter_for_a_parser_answer(real_row: dict[str, Any]) -> None:
    """The blinding is structural: there is no argument to pass a rate through.

    `_leaked_keys` above only proves the function's *output* is clean on one
    call. This proves the function *cannot* be called with a parser's row at
    all -- a caller who tried would get a TypeError, not a silently accepted
    leak. A deliberately wrong stand-in for a parser's rate is passed as an
    actual keyword argument to make the point concrete: whatever this value
    is, `task_fields` has no parameter to receive it through.

    The previous version of this test compared a float against
    `inspect.Parameter` objects (`0.99 not in signature.parameters.values()`)
    -- a comparison that is true for every possible float, so it could never
    fail regardless of what `task_fields` looked like. Calling the function
    and asserting the `TypeError` is the real claim.
    """
    deliberately_wrong_parser_rate = real_row["rate"] * 33  # nowhere near a real rate either
    signature = inspect.signature(task_fields)
    assert set(signature.parameters) == {"document_id", "source_url", "text", "kazakh_text"}
    with pytest.raises(TypeError):
        task_fields(  # type: ignore[call-arg]
            document_id=DOCUMENT_ID,
            source_url="https://adilet.zan.kz/rus/docs/" + DOCUMENT_ID,
            text=REAL_TEXT,
            kazakh_text=None,
            rate=deliberately_wrong_parser_rate,
        )
