"""Temporal КАТО resolution: a moved code resolves, an unknown one fails loudly.

The migration rows here are FIXTURES and live only in this file. They are
modelled on the 2022 oblast reform — Semey moved from Восточно-Казахстанская
область to the new Абай область — but **no mapping in `data/` is derived from
them**, because nobody has read the historical edition that would evidence it
(see `migration_coverage()`).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from kato_resolve import (
    FIELDS,
    MIGRATIONS_CSV,
    Migration,
    UnknownKato,
    known,
    load_current,
    load_migrations,
    migration_coverage,
    resolve,
)
from validate import KATO_RE

# 101010000 is г.Семей in the current classifier. 632810000 is the shape a
# pre-2022 code took, under Восточно-Казахстанская область (63).
CURRENT = {"101010000", "750000000"}
MIGRATIONS = {
    "632810000": Migration("632810000", "101010000", "2022-06-08", "https://adilet.zan.kz/EXAMPLE"),
}


def test_a_code_still_in_the_classifier_resolves_to_itself() -> None:
    assert resolve("101010000", CURRENT, MIGRATIONS) == "101010000"


def test_a_2022_era_code_resolves_to_its_current_one() -> None:
    """Resolution through the migration map, first half."""
    assert resolve("632810000", CURRENT, MIGRATIONS) == "101010000"


def test_a_code_in_neither_file_raises_rather_than_guessing() -> None:
    """Resolution through the migration map, second half.

    Not the nearest code, not the same district in another oblast, not
    "probably unchanged". A guess here returns a plausible rate for the wrong
    district, which is the failure the whole project exists to prevent.
    """
    with pytest.raises(UnknownKato) as raised:
        resolve("639999999", CURRENT, MIGRATIONS)
    assert "639999999" in str(raised.value)
    assert "not assumed unchanged" in str(raised.value)


def test_a_chain_of_two_moves_resolves_to_the_end() -> None:
    """A district that moved twice must not stop at the intermediate code."""
    chain = {
        "630000000": Migration("630000000", "632810000", "2018-01-01", "https://example/1"),
        **MIGRATIONS,
    }
    assert resolve("630000000", CURRENT, chain) == "101010000"


def test_a_migration_cycle_raises_instead_of_looping() -> None:
    cycle = {
        "111111111": Migration("111111111", "222222222", "2020-01-01", "https://example/1"),
        "222222222": Migration("222222222", "111111111", "2021-01-01", "https://example/2"),
    }
    with pytest.raises(UnknownKato) as raised:
        resolve("111111111", CURRENT, cycle)
    assert "cycle" in str(raised.value)


def test_a_migration_without_a_source_is_refused(tmp_path: Path) -> None:
    """Same rule as a rate: no row without the act that evidences it."""
    path = tmp_path / "kato_migrations.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(
            {
                "retired_kato": "632810000",
                "current_kato": "101010000",
                "effective_from": "2022-06-08",
                "kato_version_from": "NK RK 11-2021",
                "kato_version_to": "NK RK 11-2025",
                "source_url": "",
                "note": "invented",
            }
        )
    with pytest.raises(ValueError, match="no source_url"):
        load_migrations(path)


def test_the_existence_check_accepts_a_retired_code_and_rejects_an_invented_one() -> None:
    """A historical rate keyed on a retired code is correct, not an error."""
    assert known("632810000", CURRENT, MIGRATIONS)
    assert not known("639999999", CURRENT, MIGRATIONS)


def test_the_real_migration_map_has_the_agreed_header_and_no_invented_rows() -> None:
    with MIGRATIONS_CSV.open(encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == FIELDS
    assert load_migrations() == {}


def test_the_map_never_claims_to_be_complete() -> None:
    """It is empty because nobody has read the source, and it says so."""
    coverage = migration_coverage()
    assert coverage["complete"] is False
    assert ".xls" in str(coverage["note"])


def test_every_code_in_the_real_spine_resolves_to_itself() -> None:
    """The spine is the current edition, so nothing in it needs the map."""
    current = load_current()
    sample = sorted(current)[:200]
    assert all(KATO_RE.match(code) for code in sample)
    assert all(resolve(code, current, {}) == code for code in sample)
