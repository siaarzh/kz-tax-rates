"""Attach a confirmed rate to a КАТО code, or refuse and say why.

A name match alone cannot do this: 204 distinct district names cover 209 codes,
three separate «Жамбылский район» exist in three different oblasts, and titles
decline the name («по Костанайскому району») while the classifier stores the
nominative. A literal match found 0 of 19; a root match gives candidates, not
answers. So the oblast must be established first, from two sources.

**A rate on the wrong district is worse than a missing district**, because it
looks correct and carries a real citation beside it. So nothing here resolves an
ambiguity by choosing; every unmapped document is counted and named.

## The rule

1. **The oblast comes from two independent sources and they must agree**: the
   issuing body the enumeration recorded (a search facet) and the oblast the
   decision text names («Решение Уральского городского маслихата **Западно-
   Казахстанской области**»). Disagreement is a refusal, not a tie to break.
2. **Inside that oblast, exactly one district must match the name in the text.**
   The oblast fixes the first two digits of the code, which cuts ~209
   candidates to about twenty — and that is precisely what makes the three
   «Жамбылский район» distinguishable, since the ambiguity was only ever across
   oblasts.
3. **Zero matches or several is a refusal**, counted and reported.

## What is measured and what is not

That the 20 issuing bodies map onto the 20 oblast-level КАТО codes is measured
— 17 exact, 3 differing only in spacing. **That a district name resolves
uniquely inside its own oblast is expected and NOT measured.** It is the half
that can fail, and this module is written so that failure is loud.

## Names decline, so matching is on stems

A title says «в Аккольском районе» or «по Костанайскому району» while the
classifier says «Аккольский район». Comparing literally matches nothing — an
earlier attempt reported 0 of 19 mapped and the ruler was the bug. Stems are a
crude bridge, and they are only safe here because the oblast has already
narrowed the field.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from typing import Any

from extract_rates import EXTRACTED
from validate import KATO_CSV, REPO_ROOT

MAPPED = REPO_ROOT / "data" / "mapped-rates.json"
ENUMERATED = REPO_ROOT / "data" / "enumerated-decisions.json"

MAPPED_ONE = "mapped"
NO_DISTRICT = "unmapped-no-district-matched"
SEVERAL_DISTRICTS = "unmapped-several-districts-matched"
OBLAST_DISAGREEMENT = "unmapped-oblast-sources-disagree"
NO_BODY = "unmapped-no-issuing-body-recorded"
NO_OBLAST_IN_TEXT = "unmapped-no-oblast-named-in-text"
NO_OBLAST_FROM_BODY = "unmapped-body-names-no-oblast"
JURISDICTION_UNKNOWN = "unmapped-jurisdiction-type-not-stated"
JURISDICTION_DISAGREEMENT = "unmapped-jurisdiction-sources-disagree"
TITLE_CONTRADICTS_CITATION = "unmapped-title-contradicts-citation"

CITY, DISTRICT = "city", "district"

# SOURCE A — how the title names the place: «города Костаная» against
# «Костанайского района».
# The place as a NOUN — «в Уилском районе», «по Костанайскому району»,
# «Костанайского района». Not «районного маслихата», which is source B: a
# pattern matching both would make the two sources read the same words, and
# two sources reading one substring are one source. That is the same rule the
# rate readers live under.
TITLE_CITY = re.compile(r"\bгород[аеу]?\b\s*[А-ЯЁӘҒҚҢӨҰҮҺІ]")
TITLE_DISTRICT = re.compile(r"\bрайон[аеу]\b")

# SOURCE B — the kind of maslikhat the decision names: «Кокшетауского
# ГОРОДСКОГО маслихата» against «Аккольского РАЙОННОГО маслихата». Measured
# across the 136 confirmed rows before being written here: 16 городского, 90
# районного, 30 naming neither.
MASLIKHAT_CITY = re.compile(r"городско\w*\s+маслихат")
MASLIKHAT_DISTRICT = re.compile(r"районно\w*\s+маслихат")

# Words that appear in every decision and would match every district.
# Written as words and truncated to STEM at use. They were written as
# six-character prefixes once, and shortening STEM to 5 silently stopped them
# matching — so «область» was no longer a stopword and every oblast matched
# every citation. A constant that depends on another constant must be derived
# from it, not typed to agree with it.
STOPWORD_WORDS = (
    "решение",
    "маслихата",
    "область",
    "области",
    "районного",
    "район",
    "городского",
    "сельского",
    "казахстан",
    "республики",
)

# Five, not six. «Уилский район» in the classifier against «Уилского
# районного маслихата» in the text differ at the sixth character, so a
# six-character stem refused a district that is named plainly in both.
STEM = 5

CYRILLIC = r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]"
# Hyphenated as one word: «Северо-Казахстанской» split into «Северо» and
# «Казахстанской», and the second stems to «казахс», which is a stopword — so
# the oblast disappeared from a citation that names it in full.
WORD = rf"{CYRILLIC}+(?:-{CYRILLIC}+)*"

# The oblast, as the decision names it. Two orders occur: «Жамбылской области»
# and «области Абай». Anchoring on the word rather than scanning the whole text
# matters — «Решение Жамбылского районного маслихата Алматинской области» names
# TWO oblast-shaped words, and only one of them is the oblast.
# Two separate patterns, not one alternation: an alternation matched
# «маслихата области» in «маслихата области Абай» and captured the wrong word,
# because the first branch won before the second could see «Абай».
OBLAST_BEFORE = re.compile(rf"({WORD})\s+област\w*")
OBLAST_AFTER = re.compile(rf"област\w*\s+({WORD})")


def stems(text: str, minimum: int = 5) -> set[str]:
    """Word beginnings, as a bridge across Russian declension."""
    words = [word for word in re.findall(WORD, text.lower()) if len(word) >= minimum]
    return {word[:STEM] for word in words} - {word[:STEM] for word in STOPWORD_WORDS}


def oblast_codes() -> dict[str, str]:
    """oblast-level КАТО -> its name, the 20 codes an oblast body maps onto."""
    with KATO_CSV.open(encoding="utf-8", newline="") as handle:
        return {
            row["kato"]: row["name_ru"].strip()
            for row in csv.DictReader(handle)
            if row["level"] == "oblast"
        }


def districts_by_oblast() -> dict[str, list[dict[str, str]]]:
    """district-level rows, grouped by the first two digits of the code."""
    grouped: dict[str, list[dict[str, str]]] = {}
    with KATO_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["level"] == "district":
                grouped.setdefault(row["kato"][:2], []).append(row)
    return grouped


def oblast_from_body(body: str, oblasts: dict[str, str]) -> str | None:
    """SOURCE 1 — the issuing-body facet the enumeration recorded.

    minimum=4 for the same reason as source 2: «Государственные органы области
    Абай» names its oblast in a four-letter word, and a five-letter floor
    dropped it — which then read as the two sources DISAGREEING when source 1
    had simply failed to read. A source that cannot read is not a source that
    disagrees, and the difference is reported.
    """
    body_stems = stems(body, minimum=4)
    # A body naming an oblast is not a body naming a city, and the two collide
    # on stems: «Государственные органы Алматинской области» matched both
    # «Алматинская область» and «г.Алматы». The body says which kind it is.
    is_oblast = "област" in body.lower()
    matches = [
        code
        for code, name in oblasts.items()
        if name.startswith("г.") != is_oblast and stems(name, minimum=4) & body_stems
    ]
    return matches[0] if len(matches) == 1 else None


def oblast_from_text(text: str, oblasts: dict[str, str]) -> str | None:
    """SOURCE 2 — the oblast the decision names in its own citation.

    Independent of source 1: one is a search facet maintained by the registry,
    the other is the act's own words.

    It reads the word next to «область» rather than scanning the whole citation.
    Scanning found two oblasts in «Решение Жамбылского районного маслихата
    Алматинской области» — the district is named Жамбылский and an oblast is
    named Жамбылская — and refused a document that names its oblast plainly.
    """
    named = set(OBLAST_BEFORE.findall(text)) | set(OBLAST_AFTER.findall(text))
    phrase_stems = {name.lower()[:STEM] for name in named}

    # A word beside «область» that names no oblast — «маслихата» — simply
    # matches nothing below, so collecting generously costs nothing.
    matches = [
        code
        for code, name in oblasts.items()
        # A word standing next to «область» names an OBLAST, so the three
        # cities of republican significance are excluded from this pass. They
        # collide otherwise: «Алматинской области» stems to «алмат», which
        # matches both «Алматинская область» and «г.Алматы», and the citation
        # was refused as ambiguous when it is not.
        #
        # minimum=4 so «Абай» counts: an oblast may be named by a short word.
        if not name.startswith("г.") and stems(name, minimum=4) & phrase_stems
    ]

    # A city of republican significance has no «область» in its citation at
    # all: «Решение маслихата города Шымкент». It is still an oblast-level
    # code, and it is still named in the text.
    if not matches:
        text_stems = stems(text, minimum=4)
        matches = [
            code
            for code, name in oblasts.items()
            if name.startswith("г.") and stems(name.removeprefix("г."), minimum=4) & text_stems
        ]
    return matches[0] if len(matches) == 1 else None


def jurisdiction_from_title(text: str) -> str | None:
    """SOURCE A — the form the title uses for the place itself."""
    city, district = bool(TITLE_CITY.search(text)), bool(TITLE_DISTRICT.search(text))
    if city == district:
        return None
    return CITY if city else DISTRICT


def jurisdiction_from_maslikhat(text: str) -> str | None:
    """SOURCE B — the kind of maslikhat that issued it.

    Independent of source A in the same way the facet is independent of the
    text: one describes the place, the other names the institution.
    """
    city, district = bool(MASLIKHAT_CITY.search(text)), bool(MASLIKHAT_DISTRICT.search(text))
    if city == district:
        return None
    return CITY if city else DISTRICT


def candidate_kind(name: str) -> str:
    """A classifier entry is a city administration or a район, never both."""
    return CITY if ("Г.А." in name or name.strip().startswith("г.")) else DISTRICT


def strip_oblast_phrase(text: str) -> str:
    """Drop «X области» / «области X», keeping every other mention intact."""
    without = OBLAST_BEFORE.sub(" ", text)
    return OBLAST_AFTER.sub(" ", without)


def district_in_oblast(
    text: str, oblast_code: str, grouped: dict[str, list[dict[str, str]]], oblast_name: str = ""
) -> tuple[str, list[dict[str, str]]]:
    """Exactly one district in this oblast whose name the text carries.

    The oblast PHRASE is removed from the text first — «Костанайской области»,
    not every word resembling the oblast. It has already been consumed by step
    1, and leaving it in made «Жамбылской области» match the district
    «Жамбылский район» and «Карагандинской области» match «Караганда Г.А.»:
    51 documents refused as ambiguous when the second candidate was the oblast.

    Removing its STEMS instead was the obvious fix and it was wrong — it also
    deleted «Костанайского района» from «Решение маслихата Костанайского
    района Костанайской области», refusing a district whose name simply
    resembles its oblast's.
    """
    text_stems = stems(strip_oblast_phrase(text))
    candidates = [
        row for row in grouped.get(oblast_code[:2], []) if stems(row["name_ru"]) & text_stems
    ]
    if not candidates:
        return NO_DISTRICT, []
    if len(candidates) == 1:
        return MAPPED_ONE, candidates

    # Several candidates means a city and its district share a name —
    # «Костанай Г.А.» and «Костанайский район». **They are different
    # jurisdictions with different maslikhats**, so two independent sources
    # must say which, exactly as the oblast needs two.
    from_title = jurisdiction_from_title(text)
    from_maslikhat = jurisdiction_from_maslikhat(text)
    if from_title is None or from_maslikhat is None:
        return JURISDICTION_UNKNOWN, candidates
    if from_title != from_maslikhat:
        return JURISDICTION_DISAGREEMENT, candidates

    narrowed = [row for row in candidates if candidate_kind(row["name_ru"]) == from_title]
    if len(narrowed) == 1:
        return MAPPED_ONE, narrowed
    return SEVERAL_DISTRICTS, narrowed or candidates


def bodies_by_document() -> dict[str, str]:
    if not ENUMERATED.exists():
        return {}
    payload = json.loads(ENUMERATED.read_text(encoding="utf-8"))
    return {entry["document_id"]: entry["body"] for entry in payload["documents"]}


def titles_by_document() -> dict[str, str]:
    """The enumeration title, a second reading of which district an act names.

    «О понижении размера ставки налогов ... в городе Караганда» — titles read
    like this: a fixed preamble followed by the place. It is used only as a
    FALLBACK when the citation resolves nothing, and as a VETO when both
    resolve and disagree. Never as a replacement for the citation path.
    """
    if not ENUMERATED.exists():
        return {}
    payload = json.loads(ENUMERATED.read_text(encoding="utf-8"))
    return {entry["document_id"]: entry["title"] for entry in payload["documents"]}


def map_row(
    row: dict[str, Any],
    bodies: dict[str, str],
    oblasts: dict[str, str],
    grouped: dict[str, list[dict[str, str]]],
    titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One confirmed rate -> a code, or a named refusal."""
    text = " ".join(str(part) for part in (row.get("decision_ref"), row.get("sentence")) if part)
    body = bodies.get(row["document_id"])
    result: dict[str, Any] = {
        "document_id": row["document_id"],
        "rate": row["rate"],
        "year": row["year"],
        "decision_ref": row.get("decision_ref"),
        "source_url": row["source_url"],
    }

    if not body:
        return {**result, "outcome": NO_BODY}
    from_body = oblast_from_body(body, oblasts)
    from_text = oblast_from_text(text, oblasts)
    if from_text is None:
        return {**result, "outcome": NO_OBLAST_IN_TEXT, "body": body}
    if from_body is None:
        return {
            **result,
            "outcome": NO_OBLAST_FROM_BODY,
            "body": body,
            "oblast_from_text": from_text,
        }
    if from_body != from_text:
        return {
            **result,
            "outcome": OBLAST_DISAGREEMENT,
            "body": body,
            "oblast_from_body": from_body,
            "oblast_from_text": from_text,
        }

    # A city of republican significance has no district-level codes under it,
    # and its maslikhat legislates for the city itself — so the oblast-level
    # code IS the district here. Not an exception to the rule: the same rule,
    # applied where the hierarchy has one level fewer.
    if not grouped.get(from_text[:2]):
        return {
            **result,
            "outcome": MAPPED_ONE,
            "oblast_kato": from_text,
            "oblast_name": oblasts[from_text],
            "kato": from_text,
            "name_ru": oblasts[from_text],
            "candidates": [oblasts[from_text]],
        }

    outcome, candidates = district_in_oblast(text, from_text, grouped, oblasts[from_text])
    district_source = "citation" if outcome == MAPPED_ONE else None

    # The title as a SECOND district source: a fallback when the citation
    # resolves nothing, a veto when both resolve and disagree. Measured over
    # the 150 already-mapped rows: 133 agree, 0 disagree, 17 silent — the
    # title never contradicts the citation, which is the only safe shape for
    # a second source. See .claude/decisions/kato/the-title-is-a-second-
    # district-source.md.
    title_text = (titles or {}).get(row["document_id"])
    if title_text:
        title_outcome, title_candidates = district_in_oblast(
            title_text, from_text, grouped, oblasts[from_text]
        )
        if outcome == MAPPED_ONE and title_outcome == MAPPED_ONE:
            if title_candidates[0]["kato"] != candidates[0]["kato"]:
                # Both sources speak and name different districts. Neither
                # is trusted over the other — refuse rather than pick.
                outcome = TITLE_CONTRADICTS_CITATION
                candidates = candidates + title_candidates
                district_source = None
        elif outcome != MAPPED_ONE and title_outcome == MAPPED_ONE:
            outcome, candidates = title_outcome, title_candidates
            district_source = "title"

    mapped = {
        **result,
        "outcome": outcome,
        "oblast_kato": from_text,
        "oblast_name": oblasts[from_text],
        "candidates": [candidate["name_ru"] for candidate in candidates],
    }
    if outcome == MAPPED_ONE:
        mapped["kato"] = candidates[0]["kato"]
        mapped["name_ru"] = candidates[0]["name_ru"]
        mapped["name_kk"] = candidates[0]["name_kk"]
        mapped["district_source"] = district_source or "citation"
    return mapped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", type=int, default=8, help="how many refusals to print")
    arguments = parser.parse_args()

    rows = json.loads(EXTRACTED.read_text(encoding="utf-8"))["rows"]
    bodies, oblasts, grouped = bodies_by_document(), oblast_codes(), districts_by_oblast()
    titles = titles_by_document()
    results = [map_row(row, bodies, oblasts, grouped, titles) for row in rows]

    counts: dict[str, int] = {}
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1

    MAPPED.write_text(
        json.dumps(
            {
                "rule": "oblast from two agreeing sources, then exactly one district inside it",
                "counts": counts,
                # No mapped-percentage. A number mixing "we found a document"
                # with "we can attribute it" is what made 11% look like 95%.
                "rows": results,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"confirmed rates read: {len(rows)}")
    for outcome, count in sorted(counts.items()):
        print(f"  {outcome}: {count}")
    for result in [r for r in results if r["outcome"] != MAPPED_ONE][: arguments.show]:
        print(f"\n  {result['outcome']}  {result['document_id']}")
        print(f"     {str(result.get('decision_ref'))[:100]}")
        if result.get("candidates"):
            print(f"     candidates: {result['candidates']}")
    print(f"\nwrote {MAPPED.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
