"""The body enumeration, and the classifier that is now the only weak step.

Enumeration uses no word of any title, so it fails differently from the phrase
search. Classification still reads titles, which is why the unclassified pile
is sampled and read rather than described.
"""

from __future__ import annotations

import json
import urllib.error
from email.message import Message
from typing import Any

import enumerate_decisions
import pytest
from enumerate_decisions import BODIES, classify_title, enumerate_body, listing_url, parse_listing
from extract_rates import BASE_URL, FETCH_BASE_URL, cite_url, pdf_url
from validate import REPO_ROOT

SHELL_PAGE = '<html><body><div id="root"></div></body></html>'


def test_fetching_goes_to_the_legacy_host_and_citations_do_not() -> None:
    assert listing_url("165", 2025).startswith(FETCH_BASE_URL)
    assert (
        cite_url(f"{FETCH_BASE_URL}/files/pdf/1/x.kaz.pdf") == f"{BASE_URL}/files/pdf/1/x.kaz.pdf"
    )
    assert cite_url(f"{BASE_URL}/rus/docs/X") == f"{BASE_URL}/rus/docs/X"


def test_the_download_redirect_is_resolved_on_the_legacy_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class Opener:
        def open(self, request: Any, timeout: int = 0) -> Any:
            requested.append(request.full_url)
            headers = Message()
            headers["Location"] = f"{FETCH_BASE_URL}/files/pdf/1/x.rus.pdf"
            raise urllib.error.HTTPError(request.full_url, 302, "", headers, None)

    monkeypatch.setattr("extract_rates.urllib.request.build_opener", lambda *a: Opener())
    monkeypatch.setattr("extract_rates._throttle", lambda: None)
    assert pdf_url("X") == f"{FETCH_BASE_URL}/files/pdf/1/x.rus.pdf"
    assert requested == [f"{FETCH_BASE_URL}/rus/docs/X/download"]


def test_a_page_without_a_result_count_is_not_a_listing() -> None:
    assert parse_listing(SHELL_PAGE) == ([], None)
    assert parse_listing("<div>Найдено: 0 документов</div>") == ([], 0)


def test_a_non_listing_page_is_recorded_as_a_failure_not_as_emptiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(enumerate_decisions, "fetch", lambda url, attempts=3: SHELL_PAGE.encode())
    failures: list[str] = []
    assert enumerate_body("151", 2025, verbose=False, failures=failures) == []
    assert failures and "not a listing" in failures[0]


def test_an_empty_sweep_refuses_to_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(enumerate_decisions, "sweep", lambda years, bodies, failures: {})
    monkeypatch.setattr(enumerate_decisions, "ENUMERATED", tmp_path / "enumerated.json")
    monkeypatch.setattr("sys.argv", ["enumerate_decisions.py"])
    assert enumerate_decisions.main() == 1
    assert not (tmp_path / "enumerated.json").exists()


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
