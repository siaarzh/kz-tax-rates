"""The КАТО spine: stat.gov.kz -> data/kato.csv, with its provenance beside it.

SPEC.md §6.1 says this comes from a `data.egov.kz` REST API needing no
registration. **That is not true today.** Probed 2026-08-12: the v4 endpoint
returns an empty body with HTTP 200, the dataset passport carries no data link,
and the site points at an API-key cabinet, which needs an account and a
credential rather than an open fetch.

What does work is the official publisher. Бюро национальной статистики publishes
the classifier as an .xlsx on its "Статистические классификации" page. That is
the primary source, not a mirror — and it matters that it is, because a mirror is
weaker evidence and whatever spine ships must record where it came from. The two
GitHub copies considered as fallbacks turned out to hold no table at all.

The .xlsx is read with the standard library, so this repository stays free of
runtime dependencies. It is a plain single-sheet workbook: shared strings and
inline values, no formulas and no dates.

**This script writes data/kato.csv only.** It never opens data/rates.csv, which
a human alone may write (SPEC.md §12), and a test asserts that.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validate import REPO_ROOT

KATO_CSV = REPO_ROOT / "data" / "kato.csv"
KATO_SOURCE = REPO_ROOT / "data" / "kato.source.json"

# The listing page, not a file URL. stat.gov.kz serves uploads from a hashed
# path that changes with every republication, so a pinned file URL would rot
# silently — it 404s, and a fetcher that 404s reads as a network fault rather
# than as a new edition.
LISTING_URL = "https://stat.gov.kz/ru/classifiers/statistical/21/"

# The classifier itself, as approved. It is what defines the code structure and
# the `k` column, and it is cited in the provenance file so a later session can
# check this script's reading of it rather than take it on trust.
DOCUMENT_URL = (
    "https://stat.gov.kz/upload/iblock/b79/39nf76eyu5gsa6jn83vsz6u6leiiz1hv/"
    "%D0%9D%D0%9A%20%D0%A0%D0%9A%2011-2025.docx"
)

# The edition the listing page states, and the same string SPEC.md §4 already
# uses in kato_version. Recorded rather than derived: if the site publishes a
# new edition, this must be changed by a person who has read what changed.
KATO_VERSION = "NK RK 11-2025"

SPREADSHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

FIELDS = ["kato", "kato_version", "level", "type_code", "name_ru", "name_kk"]

# The segments of the code, named as НК РК 11-2025 §6.5-6.7 names them: AB
# republican level, CD oblast level, EF district level, HIJ settlements. `level`
# below says which segment the code terminates at, and nothing more.
#
# It is NOT derived from the workbook's `k` column. §6.2 and §6.5 settle what
# `k` is: the tenth sign of the code and a "признак типа местности", not a
# level. Its legend is quoted verbatim in TYPE_CODE_LEGEND and written into the
# provenance file, so no consumer has to trust a translation of it.
LEVELS = ["oblast", "district", "sub_district", "settlement"]

# §6.5, verbatim. `k` is stored as the source writes it and is never decoded
# into a vocabulary of this project's own invention.
TYPE_CODE_LEGEND = {
    "0": "республика, область",
    "1": "городская местность",
    "2": "сельская местность",
    "3": "г.а. или п.а, имеющая в подчинении населенные пункты, относящиеся к сельской местности",
    "4": "район, имеющий в подчинении населенные пункты, относящиеся к городской местности",
}


def find_workbook_url(listing_html: str) -> str:
    """The .xlsx link whose filename names КАТО, as an absolute URL."""
    matches: list[str] = re.findall(r'href="(/upload/[^"]*КАТО[^"]*\.xlsx)"', listing_html)
    if not matches:
        raise SystemExit(f"no КАТО .xlsx link on {LISTING_URL} — the page layout changed")
    if len({*matches}) > 1:
        raise SystemExit(f"several КАТО .xlsx links, refusing to guess: {sorted({*matches})}")
    # The filename is Cyrillic and the site writes it unencoded in the href.
    # urllib encodes a request line as ASCII, so an unquoted path raises
    # UnicodeEncodeError rather than any kind of HTTP error.
    return "https://stat.gov.kz" + urllib.parse.quote(matches[0])


def read_sheet(workbook: bytes) -> list[list[str]]:
    """Every row of the single sheet, as strings, shared strings resolved."""
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        strings = [
            "".join(node.text or "" for node in item.iter(SPREADSHEET_NS + "t"))
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml"))
        ]
        rows: list[list[str]] = []
        with archive.open("xl/worksheets/sheet1.xml") as sheet:
            for _, element in ET.iterparse(sheet):
                if element.tag != SPREADSHEET_NS + "row":
                    continue
                cells: list[str] = []
                for cell in element.iter(SPREADSHEET_NS + "c"):
                    value = cell.find(SPREADSHEET_NS + "v")
                    text = "" if value is None or value.text is None else value.text
                    cells.append(strings[int(text)] if cell.get("t") == "s" and text else text)
                rows.append(cells)
                element.clear()
    return rows


def level_of(code: str) -> str:
    """Which segment of the code is the last one filled in.

    Structural, so it cannot disagree with the code it describes. It is a
    weaker statement than the legal status of the place, deliberately:

      - `oblast` (CD=00) is an oblast **or** a city of republican significance
        — §6.5 separates those by AB, 10<=AB<70 against AB>70.
      - `district` (EF=00) is a район, a city of oblast significance (CD=10),
        or a district inside a city of republican significance — §6.6.
      - `sub_district` (HIJ=000) covers a п.а./с.о. and also the city itself
        when EF=10 — §6.7. The earlier name for this level was `rural_okrug`,
        which called г.Семей (101010000) a rural okrug. It is a city.

    So do not read a maslikhat's competence off this column. Counts on the
    2026-07-17 edition: 20 oblast-level rows, which is 17 oblasts and the 3
    cities of republican significance, and 209 district-level, against
    an estimate of roughly 200 maslikhats issuing a rate decision.
    """
    if len(code) != 9 or not code.isdigit():
        raise ValueError(f"not a 9-digit КАТО code: {code!r}")
    district, okrug, settlement = code[2:4], code[4:6], code[6:9]
    if district == "00" and okrug == "00" and settlement == "000":
        return LEVELS[0]
    if okrug == "00" and settlement == "000":
        return LEVELS[1]
    if settlement == "000":
        return LEVELS[2]
    return LEVELS[3]


def to_rows(sheet: list[list[str]]) -> list[dict[str, str]]:
    """Sheet rows -> spine rows, sorted by code so a rerun produces no diff."""
    header, *body = sheet
    if header[:9] != ["te", "ab", "cd", "ef", "hij", "k", "kaz_name", "rus_name", "nn"]:
        raise SystemExit(f"unexpected header, refusing to guess the columns: {header}")

    rows = [
        {
            "kato": cells[0],
            "kato_version": KATO_VERSION,
            "level": level_of(cells[0]),
            "type_code": cells[5],
            "name_ru": cells[7].strip(),
            "name_kk": cells[6].strip(),
        }
        for cells in body
        if cells and cells[0]
    ]
    codes = [row["kato"] for row in rows]
    if len(codes) != len({*codes}):
        raise SystemExit("the source lists a code twice — resolve by hand, do not deduplicate")
    return sorted(rows, key=lambda row: row["kato"])


def write(rows: list[dict[str, str]], source: dict[str, Any]) -> None:
    with KATO_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    KATO_SOURCE.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", "utf-8")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "kz-tax-rates/0 (+dataset build)"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        payload: bytes = response.read()
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workbook",
        type=Path,
        help="parse this .xlsx instead of downloading — for reruns without hitting the site",
    )
    arguments = parser.parse_args()

    if arguments.workbook:
        url = arguments.workbook.as_uri()
        payload = arguments.workbook.read_bytes()
    else:
        url = find_workbook_url(fetch(LISTING_URL).decode("utf-8"))
        payload = fetch(url)

    rows = to_rows(read_sheet(payload))
    levels = {level: sum(1 for row in rows if row["level"] == level) for level in LEVELS}
    write(
        rows,
        {
            "publisher": "Бюро национальной статистики АСПР РК (stat.gov.kz)",
            "official": True,
            "listing_url": LISTING_URL,
            "workbook_url": url,
            "kato_version": KATO_VERSION,
            "classifier_document_url": DOCUMENT_URL,
            "approved": "Приказ Председателя Комитета технического регулирования и метрологии "
            "МТИ РК № 2-НҚ от 16 января 2025 года",
            "in_force_from": "2025-02-01",
            "actualised_at": "2026-07-17",
            "type_code_legend": TYPE_CODE_LEGEND,
            "retrieved_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "rows": len(rows),
            "rows_by_level": levels,
        },
    )

    print(f"wrote {KATO_CSV.relative_to(REPO_ROOT)} — {len(rows)} rows")
    for level, count in levels.items():
        print(f"  {level}: {count}")
    print(f"wrote {KATO_SOURCE.relative_to(REPO_ROOT)} — provenance, sha256 of the workbook")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
