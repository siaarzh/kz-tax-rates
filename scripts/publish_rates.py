"""data/mapped-rates.json -> data/rates.csv. The only writer of the dataset.

Nobody hand-confirms a row here any more. What protects the data is not a
human name beside it: it is the citation (`source_url`, `decision_ref`), the
deterministic reading (two independent readers of two different-language
files agreeing, scripts/extract_rates.py), the regime check, and the
cross-check that every published row still matches the mapper's own current
conclusion (tests/test_pipeline.py,
test_published_rows_match_the_currently_mapped_set_exactly). Those are the
guards. This script writes what they have already approved.

Every value is taken from data/mapped-rates.json — never typed. The two
fields that file does not itself carry (`kato_version`, `base_rate`) are
derived from data/kato.csv and scripts/extract_rates.py's own
BASE_RATE_PERCENT table respectively, not retyped here, so this script cannot
silently drift from either source.

Deterministic and sorted, so an unchanged upstream produces a byte-identical
data/rates.csv (checked by tests/test_pipeline.py).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from extract_rates import BASE_RATE_PERCENT
from validate import FIELDS, KATO_CSV, REPO_ROOT

MAPPED_RATES_JSON = REPO_ROOT / "data" / "mapped-rates.json"
EXTRACTED_RATES_JSON = REPO_ROOT / "data" / "extracted-rates.json"
RATES_CSV = REPO_ROOT / "data" / "rates.csv"

MAPPED_OUTCOME = "mapped"

# What `json.dumps(None)` and its neighbours look like once something naively
# stringifies them. A citation this script would otherwise publish as the
# literal text "None" is not a citation — it is a missing one wearing a
# string's clothes, so it is refused here rather than written.
NULL_LIKE = {"none", "null", "nan", ""}


def _required_citation_field(entry: dict[str, Any], field: str) -> str:
    """decision_ref / source_url, refused if null or a stringified null.

    A row without a real citation does not belong in the published dataset —
    the citation is the product. This fails the whole publish loudly rather
    than dropping the row silently, because a silent drop changes the row
    count with nothing to notice it: the same failure mode the mapping and
    validation guards elsewhere in this pipeline are built to avoid.
    """
    value = entry.get(field)
    text = str(value).strip() if value is not None else ""
    if not text or text.strip().lower() in NULL_LIKE:
        raise SystemExit(
            f"kato {entry.get('kato')!r}: {field} is missing (mapped-rates.json holds "
            f"{value!r}) — refusing to publish a row without a real citation"
        )
    return text


def kato_version() -> str:
    """The single edition every row in data/kato.csv is written against.

    Read rather than typed, so a reissued classifier changes this the moment
    data/kato.csv is regenerated, instead of leaving a second, stale copy of
    the same fact sitting in this script.
    """
    with KATO_CSV.open(encoding="utf-8", newline="") as handle:
        versions = {row["kato_version"].strip() for row in csv.DictReader(handle)}
    if len(versions) != 1:
        raise SystemExit(
            f"{KATO_CSV.name} carries {len(versions)} kato_version values, expected exactly "
            f"one: {sorted(versions)}"
        )
    return next(iter(versions))


def kato_names() -> dict[str, tuple[str, str]]:
    """kato -> (name_ru, name_kk), read from the classifier.

    A fallback only: a city of republican significance resolves at the
    oblast level and data/mapped-rates.json does not attach a Kazakh name to
    those rows, so the name is completed from the same spine the code itself
    came from rather than invented.
    """
    with KATO_CSV.open(encoding="utf-8", newline="") as handle:
        return {row["kato"]: (row["name_ru"], row["name_kk"]) for row in csv.DictReader(handle)}


def extraction_method() -> str:
    """How every mapped rate was obtained, read from the extractor's own output."""
    payload = json.loads(EXTRACTED_RATES_JSON.read_text(encoding="utf-8"))
    method = payload.get("extraction_method")
    if not method:
        raise SystemExit(f"{EXTRACTED_RATES_JSON.name} carries no extraction_method")
    return str(method)


def rows_from_mapped() -> list[dict[str, str]]:
    mapped_payload = json.loads(MAPPED_RATES_JSON.read_text(encoding="utf-8"))
    names = kato_names()
    version = kato_version()
    method = extraction_method()

    rows: list[dict[str, str]] = []
    for entry in mapped_payload["rows"]:
        if entry.get("outcome") != MAPPED_OUTCOME:
            continue

        kato = str(entry["kato"])
        year = int(entry["year"])
        base_percent = BASE_RATE_PERCENT.get(year)
        if base_percent is None:
            raise SystemExit(
                f"no statutory base rate on record for {year} (extract_rates.BASE_RATE_PERCENT)"
            )

        fallback_ru, fallback_kk = names.get(kato, ("", ""))
        name_ru = entry.get("name_ru") or fallback_ru
        name_kk = entry.get("name_kk") or fallback_kk
        if not name_ru or not name_kk:
            raise SystemExit(f"{kato}: no name available from mapped-rates.json or {KATO_CSV.name}")

        rows.append(
            {
                "kato": kato,
                "kato_version": version,
                "name_ru": name_ru,
                "name_kk": name_kk,
                "rate": f"{float(entry['rate']):.2f}",
                "base_rate": f"{base_percent / 100:.2f}",
                "valid_from": f"{year}-01-01",
                "valid_to": f"{year}-12-31",
                "decision_ref": _required_citation_field(entry, "decision_ref"),
                "source_url": _required_citation_field(entry, "source_url"),
                "extraction_method": method,
            }
        )

    # Sorted on the primary key (SPEC.md §4), so a rerun with nothing changed
    # upstream writes the same bytes.
    rows.sort(key=lambda row: (row["kato"], row["kato_version"], row["valid_from"]))
    return rows


def write(rows: list[dict[str, str]], path: Path = RATES_CSV) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    rows: list[dict[str, Any]] = rows_from_mapped()
    write(rows)
    print(f"wrote {len(rows)} rows to {RATES_CSV.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
