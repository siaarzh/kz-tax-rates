"""Resolve a КАТО code as of a year, walking the migration map when it moved.

**КАТО is a temporal key, not a current-state lookup.** Kazakhstan created the
Abai, Jetisu and Ulytau oblasts in 2022, and the classifier was reissued
(ГК РК 11-2009 → НК РК 11-2021 → НК РК 11-2025). A rate keyed on a code that
was later reassigned returns a plausible number for the wrong district, with no
error and no warning. That is the failure this module exists to prevent, and it
is the same failure as a wrong rate: silent, well-formed and confident.

## Two rules, and the second is the one that will be tempting to break

1. A code that moved resolves through `data/kato_migrations.csv`, whose rows
   each carry the act that moved it.
2. **A code in neither file is an error, never a guess.** Not the nearest code,
   not the same district in another oblast, not "probably unchanged". Raising
   is the correct outcome and it is what the plan's closing check asserts.

## The map is empty, and that is a stated gap rather than an oversight

`data/kato_migrations.csv` carries its header and no rows today, exactly as
`data/rates.csv` does, for the same reason: **no row may be written that
nobody read out of a source.** The historical editions needed to derive the
mappings are published by stat.gov.kz only as `.xls` (BIFF) and `.rar`, and
nothing on this host or in the standard library reads either. Acquiring them is
a tooling decision, and until it is taken the honest state of the map is empty.

An empty map is safe: every historical code raises rather than resolving
wrongly. It is not complete, and `migration_coverage()` says so out loud.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple

from validate import KATO_CSV, REPO_ROOT

MIGRATIONS_CSV = REPO_ROOT / "data" / "kato_migrations.csv"

FIELDS = [
    "retired_kato",
    "current_kato",
    "effective_from",
    "kato_version_from",
    "kato_version_to",
    "source_url",
    "note",
]


class UnknownKato(LookupError):
    """A code is in neither the classifier nor the migration map.

    Deliberately an exception rather than a None. A caller that forgets to
    check a returned None gets a wrong district silently; one that forgets to
    catch this gets a traceback naming the code.
    """


class Migration(NamedTuple):
    retired_kato: str
    current_kato: str
    effective_from: str
    source_url: str


def load_migrations(path: Path = MIGRATIONS_CSV) -> dict[str, Migration]:
    """The map, keyed by retired code. A row without a source is not a row."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    migrations: dict[str, Migration] = {}
    for row in rows:
        if not row.get("source_url", "").strip():
            raise ValueError(f"migration {row.get('retired_kato')!r} has no source_url")
        migrations[row["retired_kato"].strip()] = Migration(
            row["retired_kato"].strip(),
            row["current_kato"].strip(),
            row["effective_from"].strip(),
            row["source_url"].strip(),
        )
    return migrations


def load_current(path: Path = KATO_CSV) -> set[str]:
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["kato"].strip() for row in csv.DictReader(handle)}


def resolve(
    kato: str,
    current: set[str] | None = None,
    migrations: dict[str, Migration] | None = None,
) -> str:
    """The code as it stands in the current classifier.

    A code still in the classifier resolves to itself. A retired one resolves
    through the map, following a chain if a district moved twice, and refusing
    a cycle rather than looping. Anything else raises.
    """
    current = load_current() if current is None else current
    migrations = load_migrations() if migrations is None else migrations

    seen: list[str] = []
    code = kato.strip()
    while code not in current:
        if code in seen:
            raise UnknownKato(f"migration cycle for {kato!r}: {' -> '.join([*seen, code])}")
        if code not in migrations:
            raise UnknownKato(
                f"{kato!r} is in neither {KATO_CSV.name} nor {MIGRATIONS_CSV.name}. "
                f"It is not resolved to the nearest code, and it is not assumed unchanged."
            )
        seen.append(code)
        code = migrations[code].current_kato
    return code


def known(kato: str, current: set[str], migrations: dict[str, Migration]) -> bool:
    """Whether a code can be resolved at all — the SPEC.md §7.5 existence check.

    Temporal resolution widens that check: a `kato` must exist in `data/kato.csv` **or** in
    the migration map. A historical rate row keyed on a retired code is
    correct; refusing it would force the dataset to rewrite history.
    """
    try:
        resolve(kato, current, migrations)
    except UnknownKato:
        return False
    return True


def migration_coverage() -> dict[str, object]:
    """What the map does and does not cover, stated rather than implied."""
    migrations = load_migrations()
    return {
        "migrations": len(migrations),
        "complete": False,
        "note": (
            "Empty or partial. The historical classifier editions are published as .xls and "
            ".rar, which nothing here reads, so no mapping has been derived from a source yet. "
            "Every unmapped historical code raises rather than resolving to a wrong district."
        ),
    }


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Resolve a КАТО code to its current form.")
    parser.add_argument("kato", nargs="*", help="one or more 9-digit codes")
    arguments = parser.parse_args()

    coverage = migration_coverage()
    print(f"migration map: {coverage['migrations']} rows, complete={coverage['complete']}")

    current, migrations = load_current(), load_migrations()
    failures = 0
    for code in arguments.kato:
        try:
            resolved = resolve(code, current, migrations)
        except UnknownKato as error:
            failures += 1
            print(f"UNRESOLVED {code}: {error}")
        else:
            moved = " (unchanged)" if resolved == code.strip() else f" -> {resolved}"
            print(f"{code}{moved}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
