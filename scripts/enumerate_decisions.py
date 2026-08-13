"""Enumerate decisions by ISSUING BODY, as a second index over the same registry.

GitHub issue 1. `discover_decisions.py` searches by phrase, and **more phrases
cannot measure what phrases miss**: a district absent because nothing matched
reads exactly like a district that kept the 4% base rate. Adding phrasings
shrinks the gap without ever saying how much is left.

**This enumerates from the institution instead.** adilet exposes its facets as
URL parameters, so every act by an oblast's bodies, in a year, of the form
"Решение", can be listed without using a single word of the title. A decision
phrased unlike anything we know is still attached to a maslikhat that exists.

The two methods fail differently, which is the whole point — the same reason
reader 4 reads a different file rather than a different pattern. Two phrasings
over one index fail together; two indexes over one registry do not.

## What this can and cannot establish

It can say what the phrase search missed, exactly, by name. It cannot prove
either method complete: classification of a listed title is still keyword work,
so a title worded unusually is counted as UNCLASSIFIED rather than silently
dropped. **That count is the honest residual** and it is reported.

## Load

One request per second, a User-Agent naming the project and a contact address,
and `pagesize=100` so a year costs about ten requests rather than a hundred.
Enumerating a registry is a different load profile from fetching 19 documents,
and being blocked is not recoverable by retrying.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
from typing import Any

from extract_rates import BASE_URL, fetch
from validate import REPO_ROOT

ENUMERATED = REPO_ROOT / "data" / "enumerated-decisions.json"

# Facet parameters, read off the search page's own links rather than guessed:
#   dt=<year>-      Дата принятия
#   kv=|1_<id>      Орган, принявший акт
#   va=РЕШ          Форма акта
# The sphere facet (ir=1_006, Финансы) is deliberately NOT used: it would drop
# any rate decision somebody filed under another sphere, and losing documents
# to a filter is the failure this module exists to measure.
FORM_DECISION = "РЕШ"

# The 20 territorial bodies, from the facet list on a search page. Each covers
# one oblast or city of republican significance, and every district maslikhat
# in it files under its oblast.
BODIES = {
    "151": "город Астана",
    "152": "Акмолинская область",
    "153": "Актюбинская область",
    "154": "Алматинская область",
    "155": "г. Алматы",
    "156": "Атырауская область",
    "157": "Восточно-Казахстанская область",
    "158": "Жамбылская область",
    "159": "Западно-Казахстанская область",
    "160": "Карагандинская область",
    "161": "Кызылординская область",
    "162": "Костанайская область",
    "163": "Мангистауская область",
    "164": "Павлодарская область",
    "165": "Северо-Казахстанская область",
    "166": "Туркестанская область",
    "167": "г. Шымкент",
    "168": "область Абай",
    "169": "область Ұлытау",
    "170": "область Жетісу",
}

# Classification of an enumerated title. Keyword work, and it is the weak step:
# it is applied to a COMPLETE listing, so what it cannot classify is counted
# rather than lost.
RATE_MARKERS = ("понижени", "ставки")
REPEAL_MARKERS = ("утративш", "утратил")

# Derived, never typed. Two constants written to agree drift apart in silence —
# the code simply stops doing what its name says, and nothing goes red.
from extract_rates import MIN_REQUEST_INTERVAL as PAUSE_SECONDS  # noqa: E402


def listing_url(body: str, year: int, page: int = 1) -> str:
    query = (
        f"dt={year}-&kv={urllib.parse.quote('|')}1_{body}&va={urllib.parse.quote(FORM_DECISION)}"
    )
    return f"{BASE_URL}/rus/search/docs/{query}&pagesize=100" + (
        f"&page={page}" if page > 1 else ""
    )


def parse_listing(page_html: str) -> tuple[list[tuple[str, str]], int]:
    """(document_id, title) pairs, and the total the site reports."""
    hits = re.findall(r'/rus/docs/([A-Z0-9]+)"[^>]*>\s*([^<]{20,300})', page_html)
    stripped = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page_html)))
    total = re.search(r"Найдено:\s*([\d ]+)\s*документ", stripped)
    return (
        [(document_id, html.unescape(title).strip()) for document_id, title in hits],
        int(total.group(1).replace(" ", "")) if total else 0,
    )


def enumerate_body(
    body: str, year: int, verbose: bool = True, failures: list[str] | None = None
) -> list[tuple[str, str]]:
    """Every Решение by one oblast's bodies in one year, by paging the listing.

    **A transport failure must not end the sweep, and must not be recorded as
    "no documents here".** The first full run died on one `SSLError:
    DECRYPTION_FAILED_OR_BAD_RECORD_MAC` after eighteen bodies of work, and a
    swallowed one would have been worse: it would have inflated the very gap
    this module measures. A page that cannot be fetched is recorded by name in
    `failures` and the sweep goes on.
    """
    found: dict[str, str] = {}
    page, total = 1, None
    while True:
        try:
            body_html = fetch(listing_url(body, year, page), attempts=5).decode("utf-8", "replace")
        except Exception as error:  # noqa: BLE001 — recorded, never counted as emptiness
            note = f"{BODIES[body]} {year} page {page}: {type(error).__name__}: {error}"
            print(f"    FETCH FAILED {note}", flush=True)
            if failures is not None:
                failures.append(note)
            return sorted(found.items())
        rows, reported = parse_listing(body_html)
        total = reported if total is None else total
        before = len(found)
        found.update(dict(rows))
        if verbose:
            print(
                f"    {BODIES[body][:28]:<28} {year} page {page:>2}: {len(found):>4}/{total}",
                flush=True,
            )
        # Stop on a page that adds nothing: the site reports a total, but a
        # count is a claim and the pages are the evidence.
        if len(found) == before or len(found) >= (total or 0) or page > 30:
            return sorted(found.items())
        page += 1
        time.sleep(PAUSE_SECONDS)


def classify_title(title: str, year: int) -> str:
    lowered = title.lower()
    if any(marker in lowered for marker in REPEAL_MARKERS):
        return "repeal"
    if all(marker in lowered for marker in RATE_MARKERS) and str(year) in title:
        return "rate-decision"
    return "other"


def sweep(
    years: list[int], bodies: list[str], failures: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for body in bodies:
        for year in years:
            for document_id, title in enumerate_body(body, year, failures=failures):
                kind = classify_title(title, max(years))
                documents.setdefault(
                    document_id,
                    {
                        "document_id": document_id,
                        "title": title,
                        "body": BODIES[body],
                        "adopted_year": year,
                        "kind": kind,
                    },
                )
            time.sleep(PAUSE_SECONDS)
    return documents


def sample_unclassified(documents: dict[str, dict[str, Any]], size: int = 40) -> dict[str, Any]:
    """Fetch a reproducible sample of the UNCLASSIFIED and read them properly.

    **Enumeration is wording-free; classification is not.** It reads titles, so
    the unclassified pile is exactly where a rate decision with an unusual
    title would sit — invisible, while every enumeration figure looks healthy.
    That is the phrase-search failure moved one step down the pipeline, and it
    would be easy to miss because the numbers above it look complete.

    So the pile is sampled and the samples are READ, with the same readers that
    read a real decision. A sample that finds none bounds the hidden set. **A
    sample that finds even one means the title classifier is the coverage
    problem and the enumeration count is not the answer.**

    The rule is fixed rather than random: sort by document id, take every
    len/size-th. Same input, same sample, so anybody can repeat it.
    """
    from extract_rates import classify, fetch, pdf_text, pdf_url  # noqa: PLC0415

    pile = sorted(key for key, value in documents.items() if value["kind"] == "other")
    if not pile:
        return {"sampled": 0, "rate_clauses_found": 0, "documents": []}
    step = max(1, len(pile) // size)
    chosen = pile[::step][:size]

    hits: list[dict[str, str]] = []
    read = 0
    for document_id in chosen:
        try:
            text = pdf_text(fetch(pdf_url(document_id)))
        except Exception as error:  # noqa: BLE001 — a fetch failure is not a finding
            hits.append({"document_id": document_id, "error": f"{type(error).__name__}: {error}"})
            continue
        read += 1
        result = classify(text)
        if result.get("sentence"):
            hits.append(
                {
                    "document_id": document_id,
                    "title": documents[document_id]["title"],
                    "sentence": result["sentence"],
                    "outcome": result["outcome"],
                }
            )
        time.sleep(PAUSE_SECONDS)

    misclassified = [h for h in hits if "sentence" in h]
    return {
        "pile_size": len(pile),
        "rule": f"sorted by document id, every {step}th, first {size}",
        "sampled": len(chosen),
        "read": read,
        "fetch_failures": len(hits) - len(misclassified),
        "rate_clauses_found": len(misclassified),
        "documents": hits,
    }


def compare(documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The number with its meaning attached: what each method missed."""
    by_phrase = {
        entry["document_id"]
        for entry in json.loads(
            (REPO_ROOT / "data" / "discovered-decisions.json").read_text(encoding="utf-8")
        )["documents"]
    }
    by_body = {key for key, value in documents.items() if value["kind"] == "rate-decision"}
    return {
        "found_by_phrase_search": len(by_phrase),
        "found_by_body_enumeration": len(by_body),
        "found_by_both": len(by_phrase & by_body),
        "found_only_by_body_enumeration": sorted(by_body - by_phrase),
        "found_only_by_phrase_search": sorted(by_phrase - by_body),
        "enumerated_total": len(documents),
        "enumerated_repeals": sum(1 for v in documents.values() if v["kind"] == "repeal"),
        "enumerated_unclassified": sum(1 for v in documents.values() if v["kind"] == "other"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=[2025, 2026])
    parser.add_argument("--bodies", nargs="+", default=sorted(BODIES))
    parser.add_argument(
        "--sample",
        type=int,
        default=0,
        help="read this many unclassified documents to estimate the classifier's error rate",
    )
    arguments = parser.parse_args()

    print(f"enumerating {len(arguments.bodies)} bodies x {len(arguments.years)} years", flush=True)
    failures: list[str] = []
    documents = sweep(arguments.years, arguments.bodies, failures)
    sample = sample_unclassified(documents, arguments.sample) if arguments.sample else None
    result = {
        "method": "issuing-body enumeration (adilet facets, no title wording used)",
        "classification_sample": sample,
        # Named, never silent. A body-year that could not be listed is not a
        # body-year with no decisions, and the difference is the whole subject.
        "incomplete_listings": failures,
        "years": arguments.years,
        "bodies": [BODIES[b] for b in arguments.bodies],
        "comparison": compare(documents),
        "documents": sorted(documents.values(), key=lambda d: d["document_id"]),
    }
    ENUMERATED.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote {ENUMERATED.relative_to(REPO_ROOT)}")
    for key, value in result["comparison"].items():
        print(f"  {key}: {value if not isinstance(value, list) else len(value)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
