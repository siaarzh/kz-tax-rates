"""Deterministic checks over data/rates.csv. No model judgement anywhere.

SPEC.md §7.5 lists six blocking checks. **All six run here now.** Four need
nothing but the row itself; the two that were deferred needed a second file or
cross-row state, and both became implementable once data/kato.csv started
existing:

  - `kato` exists in data/kato.csv        -> check_kato_exists()
  - no overlapping valid_from/valid_to    -> check_no_overlap()

`check_deferred()` still exists and now returns nothing. It is what a reader
consults to learn what is NOT checked, so it must keep being printed even when
it is empty.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RATES_CSV = REPO_ROOT / "data" / "rates.csv"
KATO_CSV = REPO_ROOT / "data" / "kato.csv"

KATO_RE = re.compile(r"^\d{9}$")

# The statutory band. НК РК art. 726 sets a 0.04 base which a maslikhat may move
# by +-50%, so nothing outside 0.02..0.06 can be a real rate.
RATE_MIN = 0.02
RATE_MAX = 0.06

FIELDS = [
    "kato",
    "kato_version",
    "name_ru",
    "name_kk",
    "rate",
    "base_rate",
    "valid_from",
    "valid_to",
    "decision_ref",
    "source_url",
    "verified_by",
    "verified_at",
]


def check_deferred() -> list[str]:
    """The SPEC.md §7.5 checks this module does not yet make.

    Empty since data/kato.csv started existing: both remaining checks needed
    that file, and both are implemented below. It stays as a function because a
    later check may be deferred again, and because main() reports it — a
    deferred check that nothing prints is a check nobody knows is missing.
    """
    return []


def known_kato(path: Path = KATO_CSV) -> set[str]:
    """Every code in the spine.

    Raises rather than returning an empty set when the file is missing. An
    existence check against an empty set passes nothing and would be visible;
    an existence check that quietly does not run passes everything.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["kato"].strip() for row in csv.DictReader(handle)}


def check_kato_exists(rows: list[tuple[int, dict[str, str]]], spine: set[str]) -> list[str]:
    """SPEC.md §7.5: every kato must exist in the spine, **or in the migration map**.

    Temporal КАТО resolution widened this. A rate for 2022 may legitimately be keyed on a code
    that has since been reassigned, and refusing it would force the dataset to
    rewrite history. So a code that resolves through
    `data/kato_migrations.csv` passes, and one that resolves nowhere fails.

    Existence only. It deliberately does NOT check that the code names a level
    a maslikhat can legislate for — that is a stricter, separate rule, and
    НК РК 11-2025 §6.6-6.7 is where its definition would come from.
    """
    from kato_resolve import known, load_migrations  # noqa: PLC0415

    migrations = load_migrations()
    return [
        f"line {line}: kato {row['kato'].strip()!r} is in neither {KATO_CSV.name} "
        f"nor kato_migrations.csv"
        for line, row in rows
        if not known(row.get("kato", "").strip(), spine, migrations)
    ]


def check_no_overlap(rows: list[tuple[int, dict[str, str]]]) -> list[str]:
    """SPEC.md §7.5: one district, one edition, one rate at a time.

    Dates are ISO, so string comparison orders them and no parsing is needed.
    A reversed range is reported first: it can never overlap anything, so
    without this the row would pass both checks while meaning nothing.
    """
    errors: list[str] = []
    groups: dict[tuple[str, str], list[tuple[int, str, str]]] = {}

    for line, row in rows:
        start, end = row.get("valid_from", "").strip(), row.get("valid_to", "").strip()
        if start > end:
            errors.append(f"line {line}: valid_from {start!r} is after valid_to {end!r}")
            continue
        key = (row.get("kato", "").strip(), row.get("kato_version", "").strip())
        groups.setdefault(key, []).append((line, start, end))

    for (kato, version), entries in groups.items():
        entries.sort(key=lambda entry: entry[1])
        for (first_line, _, first_end), (second_line, second_start, _) in zip(
            entries, entries[1:], strict=False
        ):
            if second_start <= first_end:
                errors.append(
                    f"line {second_line}: {kato} ({version}) overlaps line {first_line} — "
                    f"{second_start} starts on or before {first_end}"
                )
    return errors


def validate_row(row: dict[str, str], line: int) -> list[str]:
    errors: list[str] = []

    def fail(msg: str) -> None:
        errors.append(f"line {line}: {msg}")

    kato = (row.get("kato") or "").strip()
    if not KATO_RE.match(kato):
        fail(f"kato {kato!r} is not nine digits")

    for field in ("decision_ref", "source_url", "verified_by", "kato_version"):
        if not (row.get(field) or "").strip():
            fail(f"{field} is empty")

    raw_rate = (row.get("rate") or "").strip()
    try:
        rate = float(raw_rate)
    except ValueError:
        fail(f"rate {raw_rate!r} is not a number")
        return errors

    # The single most likely data-entry error is 3 instead of 0.03. It appeared
    # in the owner's own spreadsheet as the text '3.00%'. Reject it before the
    # band check, so the message names the actual mistake.
    if rate > 1:
        fail(f"rate {raw_rate!r} is a percentage, not a fraction (0.03, never 3)")
    elif not (RATE_MIN <= rate <= RATE_MAX):
        fail(f"rate {rate} is outside the statutory band {RATE_MIN}..{RATE_MAX}")

    return errors


def validate_file(path: Path = RATES_CSV) -> list[str]:
    if not path.exists():
        return [f"{path} does not exist"]

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            return [f"header mismatch: expected {FIELDS}, got {reader.fieldnames}"]
        # start=2: line 1 is the header, so a reported line matches the editor's.
        rows = list(enumerate(reader, start=2))

    errors: list[str] = []
    for line, row in rows:
        errors.extend(validate_row(row, line))
    errors.extend(check_kato_exists(rows, known_kato()))
    errors.extend(check_no_overlap(rows))
    return errors


def main() -> int:
    errors = validate_file()
    for error in errors:
        print(f"INVALID {error}", file=sys.stderr)
    if errors:
        print(f"{len(errors)} error(s) — nothing was written", file=sys.stderr)
        return 1
    deferred = check_deferred()
    print(f"data/rates.csv valid. Not yet checked: {'; '.join(deferred) if deferred else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
