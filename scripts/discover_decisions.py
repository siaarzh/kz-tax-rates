"""Find the rate decisions on adilet.zan.kz, and measure what was NOT found.

Extraction turned out to be the easy half. **A district missing because no
search phrase matched it looks exactly like a district that kept the 4% base
rate.** Both are absent. So this module reports what it found, what it could not
map, and by which phrase — never a single coverage number, which would hide the
distinction it is most important to see.

## How the phrase list was established, rather than assumed

Full-text search matches loosely. One phrasing returns hits that are not rate
decisions at all: the facet counts on that page read `Решение (15)`, `Закон (3)`,
`Кодекс (3)` out of 27 hits, and one of the Решения is a repeal of a 2025
decision. **"27 documents found" is not "27 decisions".**

The phrases below started from one known decision and grew from the titles the
searches themselves returned — the wording differs by maslikhat ("в 2026 году
при применении…", "…на 2026 год в X районе", "по Костанайскому району"). Each
round adds the title stems it has just seen and searches those too, stopping
after DRY_ROUNDS rounds that surface nothing new.

**That is a stopping rule, not a completeness proof.** It shows the phrases we
have stopped learning from; it cannot show a decision phrased unlike anything
seen so far. The count of districts with no document is the honest measure and
it is reported separately.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.parse
from typing import Any

from extract_rates import BASE_URL, fetch
from validate import KATO_CSV, REPO_ROOT

FOUND = REPO_ROOT / "data" / "discovered-decisions.json"

# Seeded from the one decision known to exist, then grown from result titles.
SEED_PHRASES = [
    "О понижении размера ставки налогов в 2026 году при применении специального "
    "налогового режима на основе упрощенной декларации",
    "О понижении размера ставки налогов при применении специального налогового "
    "режима на основе упрощенной декларации на 2026 год",
    "Понизить размер ставки корпоративного или индивидуального подоходного налога",
]

# Rounds returning nothing new before we stop. Two, so a single unlucky phrase
# that happens to add nothing does not end the search.
DRY_ROUNDS = 2

# A title must look like a rate reduction for the year in question...
WANTED = ("понижени", "ставки")
# ...and these are decisions ABOUT a decision, not the decision itself. Reading
# one would extract the quoted rate from the title of the act it repeals.
UNWANTED = ("утративш", "утратил", "внесении изменени")


def search(phrase: str) -> list[tuple[str, str]]:
    """(document_id, title) for one phrase. pagesize=100 avoids pagination."""
    query = urllib.parse.quote(phrase)
    url = f"{BASE_URL}/rus/search/docs/fulltext={query}&pagesize=100"
    page = fetch(url).decode("utf-8", errors="replace")
    hits = re.findall(r'/rus/docs/([A-Z0-9]+)"[^>]*>\s*([^<]{20,300})', page)
    return [(document_id, html.unescape(title).strip()) for document_id, title in hits]


def is_rate_decision(title: str, year: int) -> bool:
    lowered = title.lower()
    if any(marker in lowered for marker in UNWANTED):
        return False
    return all(marker in lowered for marker in WANTED) and str(year) in title


def title_stem(title: str, words: int = 12) -> str:
    """The opening of a title, used as the next round's search phrase."""
    return " ".join(title.split()[:words])


def discover(year: int, verbose: bool = True) -> dict[str, Any]:
    """Search, learn phrases from what comes back, stop when rounds go dry."""
    seen: dict[str, str] = {}
    rejected: dict[str, str] = {}
    by_phrase: dict[str, int] = {}
    tried: set[str] = set()
    queue = list(SEED_PHRASES)
    dry = 0

    while queue and dry < DRY_ROUNDS:
        phrase = queue.pop(0)
        if phrase in tried:
            continue
        tried.add(phrase)

        new = 0
        for document_id, title in search(phrase):
            if is_rate_decision(title, year):
                if document_id not in seen:
                    seen[document_id] = title
                    queue.append(title_stem(title))
                    new += 1
            elif document_id not in seen:
                rejected[document_id] = title
        by_phrase[phrase] = new
        dry = dry + 1 if new == 0 else 0
        if verbose:
            print(f"  +{new:>3} new · {len(seen):>3} total · {phrase[:70]}")

    return {
        "year": year,
        "phrases_tried": sorted(tried),
        "new_per_phrase": by_phrase,
        "documents": [{"document_id": key, "title": value} for key, value in sorted(seen.items())],
        "rejected_titles": [
            {"document_id": key, "title": value} for key, value in sorted(rejected.items())
        ],
    }


def district_rows() -> list[dict[str, str]]:
    """Every district-level row of the spine.

    A list, not a name-keyed dict. **District names are not unique**: 209
    district-level codes carry 204 distinct names, and «Жамбылский район»
    names three different districts in three oblasts. A dict silently kept one
    of each, which is exactly the kind of quiet loss this project is about.
    """
    import csv  # noqa: PLC0415

    with KATO_CSV.open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle) if row["level"] == "district"]


def _roots(text: str) -> set[str]:
    """Word beginnings, as a crude bridge across Russian declension.

    A title says «по Костанайскому району» or «в Курчумском районе»; the
    classifier says «Костанайский район». Comparing them literally matches
    nothing — the first version of this scoring reported 0 of 19 documents
    mapped, which looked like a coverage disaster and was a bug in the ruler.
    """
    return {word[:6] for word in re.findall(r"[А-Яа-яЁё]{5,}", text.lower())}


def coverage(result: dict[str, Any]) -> dict[str, Any]:
    """Counts, kept separate on purpose so no single number hides the gap.

    **Nothing here decides a mapping.** A shared root is a candidate, not an
    identification, and with three «Жамбылский район» in the classifier a root
    match cannot say which one a decision belongs to. Attaching a real rate to
    the wrong district is worse than publishing no rate, so the mapping stays
    unresolved and is reported as unresolved.
    """
    districts = district_rows()
    district_roots = [(row["kato"], _roots(row["name_ru"])) for row in districts]

    candidates = 0
    for document in result["documents"]:
        title_roots = _roots(document["title"])
        matches = [kato for kato, roots in district_roots if roots & title_roots]
        document["candidate_kato"] = matches
        candidates += 1 if matches else 0

    ambiguous = sum(1 for d in result["documents"] if len(d.get("candidate_kato", [])) > 1)
    return {
        "documents_found": len(result["documents"]),
        "titles_rejected_as_not_rate_decisions": len(result["rejected_titles"]),
        "documents_with_at_least_one_candidate_district": candidates,
        "documents_with_several_candidate_districts": ambiguous,
        "documents_with_no_candidate_district": len(result["documents"]) - candidates,
        "district_level_kato_codes": len(districts),
        "distinct_district_names": len({row["name_ru"].strip() for row in districts}),
        "districts_with_no_document_found": len(districts) - candidates,
        "mapping_status": "UNRESOLVED — candidates only, no document is assigned a КАТО code",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2026)
    arguments = parser.parse_args()

    print(f"searching for {arguments.year} rate decisions")
    result = discover(arguments.year)
    result["coverage"] = coverage(result)
    FOUND.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote {FOUND.relative_to(REPO_ROOT)}")
    for key, value in result["coverage"].items():
        print(f"  {key}: {value}")
    print(
        "\nA district with no document found is NOT a district that kept the base rate. "
        "The two are indistinguishable from here."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
