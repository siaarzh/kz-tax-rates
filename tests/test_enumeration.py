"""The body enumeration, and the classifier that is now the only weak step.

Enumeration uses no word of any title, so it fails differently from the phrase
search. Classification still reads titles, which is why the unclassified pile
is sampled and read rather than described.
"""

from __future__ import annotations

import json

from enumerate_decisions import BODIES, classify_title, listing_url, parse_listing
from validate import REPO_ROOT


def test_the_listing_url_uses_facets_and_no_search_words() -> None:
    """The whole point: an index that does not depend on how a title is worded."""
    url = listing_url("165", 2025)
    assert "dt=2025-" in url
    assert "kv=%7C1_165" in url
    assert "pagesize=100" in url
    assert "fulltext" not in url


def test_paging_is_only_added_after_the_first_page() -> None:
    assert "page=" not in listing_url("165", 2025)
    assert listing_url("165", 2025, 3).endswith("&page=3")


def test_the_twenty_bodies_cover_every_oblast_and_city() -> None:
    """17 oblasts and 3 cities of republican significance — a bounded list.

    This is what makes "who could have issued a decision" answerable at all.
    A phrase list has no such closure.
    """
    assert len(BODIES) == 20
    assert "Северо-Казахстанская область" in BODIES.values()
    assert "область Абай" in BODIES.values()


def test_the_listing_parser_reads_ids_titles_and_the_reported_total() -> None:
    page = (
        "<div>Найдено: 1 230 документов</div>"
        '<a href="/rus/docs/G25NN00309M">О понижении размера ставки налогов в 2026 году</a>'
    )
    rows, total = parse_listing(page)
    assert total == 1230
    assert rows == [("G25NN00309M", "О понижении размера ставки налогов в 2026 году")]


def test_the_classifier_keeps_every_document_the_phrase_search_found() -> None:
    """A regression fence between the two methods.

    If the enumeration classifier stopped recognising a title the phrase search
    already found, the comparison between methods would quietly become a
    comparison between two different questions.
    """
    documents = json.loads(
        (REPO_ROOT / "data" / "discovered-decisions.json").read_text(encoding="utf-8")
    )["documents"]
    assert documents
    assert all(classify_title(entry["title"], 2026) == "rate-decision" for entry in documents)


def test_a_repeal_is_not_counted_as_a_rate_decision() -> None:
    """Its text quotes the rate of the act it repeals, so reading it would be wrong."""
    title = (
        "О признании утратившим силу решение маслихата "
        '"О понижении размера ставки налогов в 2026 году"'
    )
    assert classify_title(title, 2026) == "repeal"


def test_an_ordinary_decision_is_unclassified_rather_than_forced() -> None:
    assert classify_title("О бюджете сельского округа на 2026-2028 годы", 2026) == "other"


def test_a_rate_decision_for_another_year_is_not_counted() -> None:
    assert classify_title("О понижении размера ставки налогов в 2025 году", 2026) == "other"
