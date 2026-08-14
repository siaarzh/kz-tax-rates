"""The end-to-end path, run against the real files.

Every fixture row here is invented and lives only in this file. No test writes
to data/rates.csv, because a rate that no human read out of a decision must
never reach the dataset (SPEC.md §12).
"""

from __future__ import annotations

import ast
import base64
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import build as build_module
import publish_rates
import pytest
from build import (
    PAGE_URL,
    REPO_URL,
    SITE,
    TAX_CODE_URL,
    artefacts,
    build,
    datapackage,
    read_aliases,
    render_index,
    render_llms_txt,
)
from fetch_kato import FIELDS as KATO_CSV_FIELDS
from fetch_kato import TYPE_CODE_LEGEND, find_workbook_url, level_of, read_sheet, to_rows
from validate import (
    FIELDS,
    KATO_RE,
    RATE_MAX,
    RATE_MIN,
    RATES_CSV,
    REPO_ROOT,
    check_deferred,
    check_kato_exists,
    check_no_overlap,
    known_kato,
    validate_file,
    validate_row,
)

VALID = {
    "kato": "750000000",
    "kato_version": "NK RK 11-2025",
    "name_ru": "г. Тестовый",
    "name_kk": "Тест қаласы",
    "rate": "0.03",
    "base_rate": "0.04",
    "valid_from": "2026-01-01",
    "valid_to": "2026-12-31",
    "decision_ref": "Тестовое решение №1 от 28.11.2025",
    "source_url": "https://adilet.zan.kz/rus/docs/EXAMPLE",
    "extraction_method": "deterministic-readers",
}


def test_the_real_csv_has_the_agreed_header() -> None:
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == FIELDS


def test_the_real_csv_validates() -> None:
    assert validate_file() == []


MAPPED_RATES_JSON = REPO_ROOT / "data" / "mapped-rates.json"


def test_published_rows_match_the_currently_mapped_set_exactly() -> None:
    """The gate that stops a wrong rate publishing under a still-correct citation.

    data/mapped-rates.json is the parser's own current conclusion about which
    document maps to which district, at what rate. This compares
    (kato, rate, source_url) tuples per year, not kato membership alone
    (plan 9, F3): a kato staying mapped while its published rate silently
    diverges from the parser's own reading is the project's stated worst
    case — a wrong rate under a correct, still-mapped citation — and a
    kato-only comparison cannot see it; every row could stay identically
    keyed while the number itself changed underneath.

    Scoped per year (plan 9, F4) because mapped-rates.json is a snapshot of
    one run and data/rates.csv is written wholesale across every year on
    file: the first year mapped-rates.json does not cover would otherwise
    read as "published but not currently mapped" against a perfectly correct
    dataset. Only years mapped-rates.json actually has an opinion about are
    compared; a published year absent from that snapshot is not flagged here.

    Reads mapped-rates.json directly rather than through a helper, so a
    missing or malformed file raises here instead of the check silently
    passing on no input — a check that cannot run has not passed.
    """
    mapped = json.loads(MAPPED_RATES_JSON.read_text(encoding="utf-8"))
    mapped_by_year: dict[str, set[tuple[str, float, str]]] = {}
    for row in mapped["rows"]:
        if row.get("outcome") != "mapped":
            continue
        year = str(row["year"])
        mapped_by_year.setdefault(year, set()).add(
            (row["kato"], round(float(row["rate"]), 4), row["source_url"])
        )

    published_by_year: dict[str, set[tuple[str, float, str]]] = {}
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            year = row["valid_from"][:4]
            published_by_year.setdefault(year, set()).add(
                (row["kato"], round(float(row["rate"]), 4), row["source_url"])
            )

    for year, mapped_set in mapped_by_year.items():
        published_set = published_by_year.get(year, set())
        missing_from_publication = sorted(mapped_set - published_set)
        published_but_not_mapped = sorted(published_set - mapped_set)
        assert not missing_from_publication, (
            f"{year}: mapped but not published: {missing_from_publication}"
        )
        assert not published_but_not_mapped, (
            f"{year}: published but not (currently) mapped: {published_but_not_mapped}"
        )


def test_build_script_runs_and_writes_json(tmp_path: Path) -> None:
    """Runs the real script, but against a copy of the tree.

    The earlier version ran it in REPO_ROOT with no SOURCE_DATE_EPOCH, so the
    committed dist/rates.json was rewritten with a wall-clock timestamp on every
    `pytest`, while every assertion stayed green. A gate that modifies the
    artefact it checks teaches everyone to ignore a modified generated file, and
    then a real regeneration carrying wrong data looks exactly like the noise.

    build.py resolves its paths from __file__, not from cwd, so isolating this
    means copying scripts/ and data/ — setting cwd alone would not have worked.
    """
    for name in ("scripts", "data"):
        shutil.copytree(REPO_ROOT / name, tmp_path / name)

    result = subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "build.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "dist" / "rates.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"


def test_a_percentage_is_rejected() -> None:
    """The error class this project exists to prevent: 3 stored instead of 0.03."""
    errors = validate_row({**VALID, "rate": "3"}, line=2)
    assert any("percentage" in error for error in errors)


def test_a_rate_outside_the_statutory_band_is_rejected() -> None:
    assert validate_row({**VALID, "rate": "0.09"}, line=2)


def test_a_row_without_a_source_is_rejected() -> None:
    assert any("source_url" in error for error in validate_row({**VALID, "source_url": ""}, line=2))


def test_a_kato_that_lost_its_leading_zero_is_rejected() -> None:
    """КАТО is a string. An integer round-trip drops the zero and shortens it."""
    assert validate_row({**VALID, "kato": "35000000"}, line=2)


def test_a_row_without_an_extraction_method_is_rejected() -> None:
    """Plan 9: extraction_method is the one of the pair that is always required."""
    errors = validate_row({**VALID, "extraction_method": ""}, line=2)
    assert any("extraction_method" in error for error in errors)


def test_coverage_is_published_and_never_claims_completeness() -> None:
    payload = build([VALID])
    coverage = payload["years"]["2026"]["coverage"]
    assert coverage["districts"] == 1
    assert coverage["complete"] is False


def test_generated_at_is_reproducible_when_pinned(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    assert build([VALID])["generated_at"] == build([VALID])["generated_at"]


def test_the_spine_is_present_and_every_code_is_nine_digits() -> None:
    codes = known_kato()
    assert len(codes) > 15000
    assert all(KATO_RE.match(code) for code in codes)


def test_no_check_is_deferred_any_more() -> None:
    """Both remaining SPEC.md §7.5 checks became implementable with kato.csv."""
    assert check_deferred() == []


def test_a_kato_absent_from_the_spine_is_rejected() -> None:
    rows = [(2, {**VALID, "kato": "999999999"})]
    assert check_kato_exists(rows, {"750000000"})


def test_a_kato_present_in_the_spine_passes() -> None:
    """The negative case alone cannot tell a working check from one that fails everything."""
    assert check_kato_exists([(2, VALID)], {VALID["kato"]}) == []


def test_two_ranges_for_one_district_and_edition_are_rejected() -> None:
    rows = [
        (2, {**VALID, "valid_from": "2026-01-01", "valid_to": "2026-12-31"}),
        (3, {**VALID, "valid_from": "2026-06-01", "valid_to": "2027-05-31"}),
    ]
    assert any("overlaps" in error for error in check_no_overlap(rows))


def test_touching_ranges_overlap_because_the_dates_are_inclusive() -> None:
    """valid_to is the last valid day, so a next range starting that day is two rates in one day."""
    rows = [
        (2, {**VALID, "valid_from": "2026-01-01", "valid_to": "2026-12-31"}),
        (3, {**VALID, "valid_from": "2026-12-31", "valid_to": "2027-12-31"}),
    ]
    assert any("overlaps" in error for error in check_no_overlap(rows))


def test_consecutive_years_for_one_district_are_accepted() -> None:
    rows = [
        (2, {**VALID, "valid_from": "2026-01-01", "valid_to": "2026-12-31"}),
        (3, {**VALID, "valid_from": "2027-01-01", "valid_to": "2027-12-31"}),
    ]
    assert check_no_overlap(rows) == []


def test_the_same_range_under_two_kato_editions_is_not_an_overlap() -> None:
    """The key is (kato, kato_version) — a re-coded district is a different row, not a clash."""
    rows = [
        (2, VALID),
        (3, {**VALID, "kato_version": "NK RK 11-2021"}),
    ]
    assert check_no_overlap(rows) == []


def test_a_reversed_range_is_reported_rather_than_silently_never_overlapping() -> None:
    rows = [(2, {**VALID, "valid_from": "2026-12-31", "valid_to": "2026-01-01"})]
    assert any("after valid_to" in error for error in check_no_overlap(rows))


def _markup_only(page: str) -> str:
    """The page with every script BODY emptied, opening tags kept.

    Scanning the raw page for attributes reads the inlined JavaScript as markup:
    `i<all.length; ...` matches an opening tag and swallows everything up to the
    next `>`. Emptying the bodies leaves the one thing that matters here, which
    is `<script src=`, still visible in the opening tag.
    """
    return re.sub(r"(<script\b[^>]*>).*?(</script>)", r"\1\2", page, flags=re.S)


# Attributes a browser dereferences on its own. `rel` is not one of them, and
# neither is a `content` on a meta: og:url naming this page is a declaration,
# not a fetch.
FETCHING_ATTRS = r"\b(src|srcset|href|poster|data|action|formaction)\s*=\s*\"([^\"]*)\""


def test_the_page_inlines_the_data_and_requests_nothing() -> None:
    """SPEC.md §8.1: one self-contained file, no framework.

    A page that fetched rates.json would show an empty table under file://,
    indistinguishable from a complete table of nothing.

    Every attribute the browser would dereference must be a fragment, a data:
    URI, or the canonical link. Canonical is the one absolute URL allowed here
    and it is allowed on a measured ground rather than a stylistic one: no
    browser fetches it. The favicon is inlined as base64 for the same reason a
    relative one was rejected, that a same-origin request is still a request.
    """
    page = render_index(build([VALID]))
    assert VALID["kato"] in page
    assert "<script src" not in page

    for tag in re.finditer(r"<([a-z]+)\b([^>]*)>", _markup_only(page)):
        name, attrs = tag.group(1), tag.group(2)
        rel = re.search(r'\brel="([^"]*)"', attrs)
        for attribute, value in re.findall(FETCHING_ATTRS, attrs):
            if value.startswith("#") or value.startswith("data:"):
                continue
            assert name == "link" and rel and rel.group(1) == "canonical", (
                f"<{name} {attribute}={value!r}> makes the page fetch something"
            )


def test_a_closing_script_tag_in_the_data_cannot_end_the_tag_early() -> None:
    hostile = {**VALID, "name_ru": "</script><b>x"}
    page = render_index(build([hostile]))
    assert "</script><b>" not in page.split('id="payload"')[1].split("</script>")[0]


def test_the_page_says_the_table_is_incomplete_rather_than_looking_finished() -> None:
    empty = render_index(build([]))
    assert "нет ни одной ставки" in empty
    assert "неполная" in render_index(build([VALID]))


def test_the_page_carries_the_citation_of_every_row() -> None:
    """A rate without its decision beside it is the thing this project refuses to publish.

    This asserts the citation reaches the page and that the render path links
    it. It cannot prove what a browser draws — that needs a browser, and the
    check is only as strong as that limit.
    """
    page = render_index(build([VALID]))
    assert VALID["source_url"] in page
    assert VALID["decision_ref"] in page
    assert "link.href = rate.source_url" in page


# Anything that would make the browser open a connection: a link with a rel
# that fetches, a script with a src, a CSS import, a font declaration, an
# embedded media element, or an absolute URL inside a CSS url(). A source_url
# inside the JSON payload is none of these: it is data the page prints as a
# link, and it is the whole point of the project.
#
# `<link\b` used to stand here as a blanket ban, which was a proxy for the rule
# rather than the rule. rel=canonical and a data: rel=icon fetch nothing, and
# the page needs both to be findable. The rels below are the ones that do fetch;
# the companion test above is what actually holds the line, by walking every
# dereferenced attribute rather than pattern-matching the tags.
EXTERNAL = (
    r"<link[^>]*\brel=\"(?:stylesheet|preload|prefetch|preconnect|dns-prefetch"
    r"|modulepreload|manifest|prerender)\"",
    r"<script[^>]*\bsrc\s*=",
    r"@import",
    r"@font-face",
    r"url\(\s*['\"]?https?:",
    r"<(?:img|iframe|object|embed|video|audio|source|track)\b",
)


def test_the_built_page_references_no_external_script_style_or_font() -> None:
    """The invariant that had no guard, which is how it would quietly regress.

    MiniSearch is vendored and inlined for exactly this reason. A CDN tag would
    look fine on a laptop with a network and would leave the page with no search
    for the reader who has none, and offline is the case this page is built for.
    """
    page = render_index(build([VALID]))
    markup = _markup_only(page)
    for pattern in EXTERNAL:
        assert not re.search(pattern, markup, re.IGNORECASE), pattern
    for host in ("cdn.jsdelivr.net/npm", "unpkg.com", "fonts.googleapis.com", "cdnjs"):
        assert host not in page


def test_the_search_library_is_inlined_rather_than_fetched() -> None:
    page = render_index(build([VALID]))
    assert "MiniSearch" in page
    assert "/*MINISEARCH*/" not in page


def test_the_alias_index_is_inlined_and_the_two_places_named_medeu_both_survive() -> None:
    """Two unrelated places are called Медеу and they resolve to different rates.

    One is a rural okrug in области Абай, the other is a borough of Алматы.
    Preferring either one silently would answer a question the reader did not
    ask, so both must reach the page.
    """
    page = render_index(build([VALID]))
    assert "/*ALIASES*/" not in page
    aliases = json.loads(page.split('id="aliases">')[1].split("</script>")[0].replace("<\\/", "</"))
    medeu = {row[0]: row[3] for row in aliases if row[1].startswith("Медеу") or row[1] == "с.Медеу"}
    assert medeu["751710000"] == "750000000"
    assert medeu["103245000"] == "103200000"


def test_every_decision_link_opens_in_a_new_tab_and_cannot_reach_back() -> None:
    """A citation followed mid-search must not cost the reader their place."""
    page = render_index(build([VALID]))
    assert 'link.target = "_blank"' in page
    assert 'link.rel = "noopener noreferrer"' in page
    assert 'kazLink.target = "_blank"' in page
    assert 'kazLink.rel = "noopener noreferrer"' in page


def test_the_kazakh_url_is_derived_and_omitted_when_the_shape_does_not_match() -> None:
    """adilet serves the same act at /rus/ and /kaz/, so the second URL is derived.

    A source_url of another shape gets no Kazakh link at all. Guessing one would
    publish a plausible URL that 404s, and that reads as a fact until somebody
    clicks it.
    """
    page = render_index(build([VALID]))
    body = page.split("function kazakhUrl")[1].split("}")[0]
    assert 'url.replace("/rus/", "/kaz/")' in body
    assert "null" in body
    assert "data/rates.csv" not in body


def test_the_kazakh_link_is_marked_as_kazakh_for_a_screen_reader() -> None:
    page = render_index(build([VALID]))
    assert 'kazLink.setAttribute("lang", "kk")' in page
    assert "Текст решения на казахском языке" in page


def test_an_alias_carries_no_rate_of_its_own() -> None:
    """An alias row is [kato, name_ru, name_kk, resolves_to]. There is no rate in it.

    If a rate ever appeared here it would be one nobody read out of a decision,
    which is the failure the whole project is built to refuse.
    """
    for row in read_aliases():
        assert len(row) == 4
        assert all(isinstance(field, str) for field in row)


def test_the_json_carries_the_licence_and_the_notice() -> None:
    """A consumer who reads only the JSON must still get the terms.

    Tax consulting is a regulated activity in Kazakhstan and publishing
    structured public facts is not. The notice is what keeps that line visible,
    so it cannot live in the README alone.
    """
    payload = build([VALID])
    assert payload["licence"] == "MIT"
    assert payload["not_tax_advice"].startswith("Not tax advice.")
    assert "налоговой консультацией" in payload["not_tax_advice_ru"]


def test_the_page_states_the_terms_from_the_payload_not_from_the_template() -> None:
    """The page and the JSON must not be able to state different terms."""
    template = (REPO_ROOT / "scripts" / "index.template.html").read_text(encoding="utf-8")
    assert "payload.not_tax_advice_ru" in template
    assert "payload.licence" in template
    assert "MIT" not in template


# ---------------------------------------------------------------------------
# Several years at once.
#
# The page draws the year columns in JavaScript, and CI has no browser, so the
# tests below pin the part that decides: page_view() and the view blob the page
# is handed. Everything a year column does is downstream of that blob, and the
# one thing the blob cannot state — that a missing rate is never drawn as the
# base rate — is checked against the renderer's own source.
# ---------------------------------------------------------------------------

TEMPLATE = REPO_ROOT / "scripts" / "index.template.html"


def _for_year(year: int, kato: str = "750000000", rate: str = "0.03") -> dict[str, str]:
    return {
        **VALID,
        "kato": kato,
        "rate": rate,
        "valid_from": f"{year}-01-01",
        "valid_to": f"{year}-12-31",
        "decision_ref": f"Тестовое решение за {year} год",
    }


def _view_of(page: str) -> dict[str, Any]:
    """The view blob the built page was handed, read back out of the page."""
    blob = re.search(r'id="view">(.*?)</script>', page, re.S)
    assert blob, "the page carries no view blob"
    loaded: dict[str, Any] = json.loads(blob.group(1))
    return loaded


def _pin(monkeypatch: pytest.MonkeyPatch, year: int) -> None:
    """Pin the build clock, so the current year is a fact of the fixture."""
    monkeypatch.setenv("SOURCE_DATE_EPOCH", str(int(datetime(year, 6, 1, tzinfo=UTC).timestamp())))


def test_one_year_draws_no_year_column(monkeypatch: pytest.MonkeyPatch) -> None:
    """Today's state, and it must stay a page with no hint that a feature exists."""
    _pin(monkeypatch, 2026)
    view = _view_of(render_index(build([_for_year(2026)])))
    assert view["years"] == ["2026"]
    assert view["future_note_ru"] == ""
    # The columns are gated on this one expression; nothing else turns them on.
    assert "var multi = yearsAsc.length > 1;" in TEMPLATE.read_text(encoding="utf-8")


def test_two_years_are_drawn_oldest_first_with_the_current_one_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pin(monkeypatch, 2026)
    page = render_index(build([_for_year(2026), _for_year(2025, rate="0.04")]))
    view = _view_of(page)
    assert view["years"] == ["2025", "2026"], "oldest to newest, left to right"
    assert view["current_year"] == "2026"
    # Which column is the loud one is decided by that year, in the renderer.
    assert 'if (year === currentYear) { return "cur"; }' in TEMPLATE.read_text(encoding="utf-8")


def test_the_current_year_comes_from_the_build_not_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuild of old data must produce the old page, not today's ranking.

    Reading the reader's clock instead would silently re-rank an archived
    build's columns, and would make every test here expire.
    """
    _pin(monkeypatch, 2025)
    view = _view_of(render_index(build([_for_year(2026), _for_year(2025, rate="0.04")])))
    assert view["current_year"] == "2025"


def test_a_future_year_says_the_column_is_only_partly_filled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Art. 726 НК РК: the next year's rate is adopted by 1 December, so until
    that date passes the column is partly decided and mostly not."""
    _pin(monkeypatch, 2025)
    page = render_index(build([_for_year(2026), _for_year(2025, rate="0.04")]))
    assert _view_of(page)["future_note_ru"] == (
        "Ставки на следующий год маслихаты принимают до 1 декабря. "
        "Пока эта дата не прошла, столбец заполнен лишь частично."
    )
    assert "маслихаты принимают до 1 декабря" in page


def test_the_sentence_is_absent_when_no_future_column_is_drawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It qualifies a column. With no future column there is nothing to qualify,
    and a warning shown where it does not apply is noise that trains a reader to
    skip the one that does."""
    _pin(monkeypatch, 2026)
    two_past = render_index(build([_for_year(2026), _for_year(2025, rate="0.04")]))
    one_year = render_index(build([_for_year(2026)]))
    for page in (two_past, one_year):
        assert _view_of(page)["future_note_ru"] == ""
        assert "1 декабря" not in page


def test_a_year_the_district_has_no_decision_for_is_absent_not_filled_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty cell means no decision was read. It never means the base rate.

    Two halves: the build invents nothing, and the renderer's missing branch
    returns before it can reach a rate at all.
    """
    _pin(monkeypatch, 2026)
    only_2025 = _for_year(2025, kato="751710000", rate="0.02")
    payload = build([_for_year(2026), only_2025])
    katos_2026 = [entry["kato"] for entry in payload["years"]["2026"]["rates"]]
    assert "751710000" not in katos_2026, "the build filled in a year it was not given"

    template = TEMPLATE.read_text(encoding="utf-8")
    branch = re.search(r"if \(!rate\) \{(.*?)\n    \}", template, re.S)
    assert branch, "the missing-rate branch is not where this test thinks it is"
    body = branch.group(1)
    assert "нет данных" in body, "a missing rate must read as no data"
    assert "base" not in body and "rate.rate" not in body, (
        "the missing-rate branch can reach a rate; it must state none"
    )
    assert "percent(" not in body


DASHES = ("—", "–", "―", "‒")


def test_no_published_artefact_carries_an_em_or_en_dash_in_any_language() -> None:
    """A typographic dash is not in the house style, in Russian or in English.

    The head is checked as well as the body now, because a description and an
    og:title are read by a person in a search result and a share card, which is
    exactly where the house style is visible.

    The template's own HTML comments are excluded and nothing else is: they are
    stripped by no build step, but they are also not text anyone is served.
    """
    payload = build([_for_year(2026)])
    page = render_index(payload)
    served = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    for dash in DASHES:
        assert dash not in served, f"the rendered page carries {dash!r}"
        assert dash not in render_llms_txt(payload), f"llms.txt carries {dash!r}"
        assert dash not in json.dumps(datapackage(), ensure_ascii=False), (
            f"datapackage.json carries {dash!r}"
        )


# ---------------------------------------------------------------------------
# Findability: the structured data, the ordinary metadata, and the attribution.
#
# All of it is emitted by render_head() and page_view() rather than written into
# the template, so all of it is checked against the payload it came from. The
# failure these guard against is metadata that was true of an earlier build:
# a title naming one year while the data holds two, a distribution list missing
# the file the build actually wrote, a canonical pointing at a moved page.
# ---------------------------------------------------------------------------


def _jsonld(page: str) -> list[dict[str, Any]]:
    """Every ld+json block in the page, parsed. Unparseable is a failure here."""
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    assert blocks, "the page carries no JSON-LD at all"
    return [json.loads(block.replace("<\\/", "</")) for block in blocks]


def _typed(page: str, wanted: str) -> dict[str, Any]:
    found = [block for block in _jsonld(page) if block.get("@type") == wanted]
    assert len(found) == 1, f"expected exactly one {wanted} block, found {len(found)}"
    return found[0]


def test_the_dataset_block_parses_and_is_populated_from_the_payload() -> None:
    payload = build([_for_year(2026)])
    dataset = _typed(render_index(payload), "Dataset")

    assert dataset["@context"] == "https://schema.org"
    assert dataset["license"] == "https://spdx.org/licenses/MIT.html"
    assert dataset["isAccessibleForFree"] is True
    assert dataset["url"] == PAGE_URL
    assert dataset["dateModified"] == payload["generated_at"]
    assert dataset["creator"] == {
        "@type": "Person",
        "name": "Serzhan Akhmetov",
        "url": "https://github.com/siaarzh",
    }
    assert dataset["spatialCoverage"]["address"]["addressCountry"] == "KZ"
    assert dataset["temporalCoverage"] == "2026-01-01/2026-12-31"
    assert dataset["citation"]["url"] == TAX_CODE_URL
    assert "726" in str(dataset["citation"]["name"])
    # Both languages, because the reader and the crawler search in different ones.
    keywords = dataset["keywords"]
    assert any(re.search("[а-яё]", word, re.I) for word in keywords)
    assert any(re.fullmatch("[ -~]+", word) for word in keywords)


def test_the_temporal_coverage_spans_every_year_present() -> None:
    """Derived, so a second year of data cannot leave the first year's span behind."""
    payload = build([_for_year(2026), _for_year(2027, kato="751000000")])
    assert _typed(render_index(payload), "Dataset")["temporalCoverage"] == "2026-01-01/2027-12-31"


def test_every_published_artefact_has_a_distribution() -> None:
    """The distribution list is what a dataset crawler follows, so a file the
    build wrote and the metadata did not mention is a file nobody finds.

    Checked against artefacts(), which is the same list write(), the page's own
    link row and llms.txt are built from.
    """
    payload = build([_for_year(2026), _for_year(2027, kato="751000000")])
    dataset = _typed(render_index(payload), "Dataset")
    declared = {entry["contentUrl"]: entry for entry in dataset["distribution"]}

    for artefact in artefacts(payload):
        assert artefact["url"] in declared, artefact["url"]
        entry = declared[artefact["url"]]
        assert entry["@type"] == "DataDownload"
        assert entry["encodingFormat"] == artefact["format"]

    # Both year files, the CSV and the frictionless descriptor, named explicitly
    # rather than only via the loop, so a change to artefacts() that dropped one
    # would not silently take the assertion with it.
    for url in (
        PAGE_URL + "rates.json",
        PAGE_URL + "rates-2026.json",
        PAGE_URL + "rates-2027.json",
        SITE + "data/rates.csv",
        SITE + "datapackage.json",
    ):
        assert url in declared, url
    assert declared[SITE + "data/rates.csv"]["encodingFormat"] == "text/csv"


def test_the_page_declares_a_description_and_a_canonical() -> None:
    page = render_index(build([_for_year(2026)]))
    description = re.search(r'<meta name="description" content="([^"]+)">', page)
    assert description, "the page has no meta description"
    # Written for a person searching in Russian for their own district's rate.
    assert re.search("[а-яё]", description.group(1), re.I)
    assert len(description.group(1)) > 80
    assert f'<link rel="canonical" href="{PAGE_URL}">' in page
    assert f'<meta property="og:url" content="{PAGE_URL}">' in page
    assert '<meta property="og:locale" content="ru_RU">' in page
    assert '<meta property="og:locale:alternate" content="kk_KZ">' in page
    assert '<meta name="twitter:card" content="summary">' in page


def test_the_page_states_no_image_it_does_not_have() -> None:
    """An og:image pointing at a file nobody drew renders as a broken card.

    Same rule that refused an invented jsDelivr URL: a tag is a claim.
    """
    page = render_index(build([_for_year(2026)]))
    assert "og:image" not in page
    assert "twitter:image" not in page


def test_the_title_carries_the_years_the_payload_actually_holds() -> None:
    one = render_index(build([_for_year(2026)]))
    assert re.search(r"<title>[^<]*2026[^<]*</title>", one)
    assert "2027" not in one[: one.index("</head>")]

    two = render_index(build([_for_year(2026), _for_year(2027, kato="751000000")]))
    title = re.search(r"<title>([^<]*)</title>", two)
    assert title and "2026 · 2027" in title.group(1)


def test_the_favicon_is_inlined_rather_than_fetched() -> None:
    """A relative href to favicon.svg is still an HTTP request, so it is base64.

    Measured, not assumed: the rule is zero requests, not zero third parties.
    """
    page = render_index(build([_for_year(2026)]))
    icon = re.search(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)">', page)
    assert icon, "the page references no favicon"
    assert icon.group(1).startswith("data:image/svg+xml;base64,")
    decoded = base64.b64decode(icon.group(1).split(",", 1)[1])
    assert decoded == (REPO_ROOT / "dist" / "favicon.svg").read_bytes()


def test_no_favicon_tag_is_emitted_when_the_file_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A favicon pointing at nothing is the same failure as an absent og:image."""
    monkeypatch.setattr(build_module, "DIST", REPO_ROOT / "nowhere")
    assert 'rel="icon"' not in render_index(build([_for_year(2026)]))


def test_the_theme_colour_matches_the_page_it_frames() -> None:
    """Two copies of one fact, so they are compared rather than trusted."""
    page = render_index(build([_for_year(2026)]))
    css = page[page.index("<style>") : page.index("</style>")]
    light = re.search(r'content="(#[0-9a-f]{6})" media="\(prefers-color-scheme: light\)"', page)
    dark = re.search(r'content="(#[0-9a-f]{6})" media="\(prefers-color-scheme: dark\)"', page)
    assert light and dark

    def expand(value: str) -> str:
        short = re.search(r"--bg:\s*(#[0-9a-f]{3,6});", value)
        assert short
        hex_ = short.group(1)[1:]
        return "#" + ("".join(c * 2 for c in hex_) if len(hex_) == 3 else hex_)

    assert expand(css[: css.index("@media")]) == light.group(1)
    assert expand(css[css.index("prefers-color-scheme: dark") :]) == dark.group(1)


def test_the_search_action_is_declared_because_the_page_honours_a_query_string() -> None:
    """Declared only because ?q= works. Both halves are checked, in one test,
    because either alone is the failure: a SearchAction the page ignores sends
    a reader from a search engine to an unfiltered table and calls it a result.
    """
    page = render_index(build([_for_year(2026)]))
    site = _typed(page, "WebSite")
    action = site["potentialAction"]
    assert action["@type"] == "SearchAction"
    assert action["target"]["urlTemplate"] == PAGE_URL + "?q={search_term_string}"
    assert action["query-input"] == "required name=search_term_string"

    # The renderer's own source, because CI has no browser to run it in.
    body = page[page.index("<body>") :]
    assert 'queryParam("q")' in body
    assert "location.search" in body
    # The hash still wins: a permalink to one row is more specific than a query.
    assert "hash || queryParam" in body


def test_the_footer_names_the_author_and_links_the_repository() -> None:
    """Asked for directly: nobody should have to infer this from the URL bar."""
    page = render_index(build([_for_year(2026)]))
    view = _view_of(page)
    assert view["author"] == "Serzhan Akhmetov"
    assert view["repo_url"] == REPO_URL

    body = page[page.index("<body>") :]
    assert "view.author" in body
    assert "view.repo_url" in body
    assert "view.provenance_ru" in body
    # The licence still comes from the payload, never from the template.
    assert 'attribution.appendChild(document.createTextNode(". Лицензия " + payload.licence' in body


def test_the_footer_states_the_extraction_method_derived_not_asserted() -> None:
    """Counted from extraction_method, so it corrects itself the day a new method
    is added rather than standing as a stale claim. There is no human-verification
    tier any more: the sentence never promises a person checked the row."""
    machine = _for_year(2026)
    view = _view_of(render_index(build([machine])))
    assert "решений маслихатов программой" in str(view["provenance_ru"])
    assert "человек" not in str(view["provenance_ru"])

    # A row whose extraction_method is not in MACHINE_EXTRACTION_METHODS is not
    # counted as machine-extracted, and the sentence says so rather than
    # silently rounding it in.
    unknown_method = {**_for_year(2026), "extraction_method": "unrecognised-method"}
    mixed = _view_of(render_index(build([machine, {**unknown_method, "kato": "751000000"}])))
    assert "extraction_method" in str(mixed["provenance_ru"])

    assert _view_of(render_index(build([])))["provenance_ru"] == ""


def test_the_page_lists_its_machine_readable_files_near_the_top() -> None:
    """Visible on the page, not only in the metadata, and above the search box.

    The links are relative, so a copy opened from file:// still resolves them.
    """
    page = render_index(build([_for_year(2026)]))
    view = _view_of(page)
    assert [entry[0] for entry in view["files"]] == [
        "rates.json",
        "rates-2026.json",
        "data/rates.csv",
        "datapackage.json",
        "llms.txt",
    ]
    for entry in view["files"]:
        assert not entry[1].startswith("http"), entry

    body = page[page.index("<body>") :]
    assert body.index('id="files"') < body.index('class="search"')


def test_the_verification_count_reaches_the_published_json() -> None:
    """A consumer of rates.json alone cannot see extraction_method on the row
    entries themselves, so the count is published at the top level."""
    other = {**VALID, "kato": "751000000"}
    payload = build([VALID, other])
    assert payload["verification"] == {"rows": 2, "machine_extracted": 2}
    assert build([])["verification"] == {"rows": 0, "machine_extracted": 0}


def test_machine_extracted_is_read_from_extraction_method_not_asserted() -> None:
    """A method never added to MACHINE_EXTRACTION_METHODS does not count, so a
    new extraction path has to be taught to that set explicitly."""
    unrecognised = {**VALID, "extraction_method": "unrecognised-method"}
    assert build([unrecognised])["verification"]["machine_extracted"] == 0

    recognised = {**VALID, "kato": "751000000"}
    mixed = build([unrecognised, recognised])["verification"]
    assert mixed == {"rows": 2, "machine_extracted": 1}


def test_the_datapackage_types_every_column_of_both_csvs() -> None:
    """A column added without a type would otherwise be published untyped and unnoticed."""
    package = datapackage()
    resources = {resource["name"]: resource for resource in package["resources"]}
    assert [f["name"] for f in resources["rates"]["schema"]["fields"]] == FIELDS
    assert [f["name"] for f in resources["kato"]["schema"]["fields"]] == KATO_CSV_FIELDS


def test_the_datapackage_declares_the_licence_and_says_what_it_does_not_cover() -> None:
    licence = datapackage()["licenses"][0]
    assert licence["name"] == "MIT"
    assert "not copyrightable" in licence["title"]


def test_the_datapackage_keys_rates_on_the_spec_primary_key() -> None:
    """SPEC.md §4: (kato, kato_version, valid_from)."""
    rates = datapackage()["resources"][0]
    assert rates["schema"]["primaryKey"] == ["kato", "kato_version", "valid_from"]


def test_llms_txt_states_the_fraction_and_string_traps() -> None:
    text = render_llms_txt(build([VALID]))
    assert "FRACTIONS" in text
    assert "STRING of nine digits" in text
    assert "Not tax advice." in text


def test_llms_txt_gives_a_fetch_url_for_every_published_artefact() -> None:
    """An agent reads this file to find out what to fetch, so it must list it all.

    The repository is public and every one of these URLs answered 200 on
    2026-08-13. The file used to warn NOT FETCHABLE TODAY, which was true of a
    private remote and became a wrong fact the moment the remote went public.
    A stale warning is not the safe side of this; it is the same failure as a
    stale rate.
    """
    payload = build([VALID])
    text = render_llms_txt(payload)
    for artefact in artefacts(payload):
        assert artefact["url"] in text, artefact["url"]
    assert "NOT FETCHABLE" not in text
    assert "PRIVATE repository" not in text
    assert "<user>" not in text
    assert REPO_URL in text


def test_llms_txt_states_what_an_absent_district_means_and_how_many_rows_were_extracted() -> None:
    """A gap is not the base rate. Counted from the data rather than asserted."""
    text = render_llms_txt(build([VALID, {**VALID, "kato": "751000000"}]))
    assert "does NOT mean the district charges the base rate" in text.replace("\n", " ")
    assert "Rows: 2. Extracted from the decision by rule: 2." in text


def test_llms_txt_provenance_does_not_claim_a_human_verification_step() -> None:
    """There is no human-verification tier. The text must not invent one."""
    text = render_llms_txt(build([VALID]))
    assert "verified_by" not in text
    assert "human_verified" not in text
    assert "extraction_method" in text
    assert "Extracted from the decision by rule: 1." in text


def test_the_jsdelivr_url_names_the_branch_this_repository_actually_uses() -> None:
    """SPEC.md §8.2 writes @main. The default branch is master, and jsDelivr is literal."""
    text = render_llms_txt(build([]))
    assert "kz-tax-rates@master/dist/rates.json" in text
    assert "@main/" not in text


def test_readme_district_count_matches_the_published_row_count() -> None:
    """F5: README typed 127 by hand while the real CSV held 148 districts.

    Nothing in the build emits into README.md, so the count still has to be
    typed there — this at least fails the day it drifts again, rather than
    standing silently wrong the way it did this time.
    """
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        published = len({row["kato"] for row in csv.DictReader(handle)})
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert f"**{published} districts**" in readme, (
        f"README does not say '**{published} districts**' — it has drifted from data/rates.csv"
    )


def test_dist_is_tracked_not_ignored() -> None:
    """Pages and jsDelivr serve dist/ straight from the repo (SPEC.md §3)."""
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert not any(line.strip() in {"dist", "dist/"} for line in ignore.splitlines())


def _workbook(rows: list[list[str]]) -> bytes:
    """The smallest .xlsx read_sheet accepts: shared strings plus one sheet.

    Built here rather than committed as a binary fixture, so the expected shape
    is readable in the diff. read_sheet opens only these two members.
    """
    strings = sorted({cell for row in rows for cell in row})
    shared = "".join(f"<si><t>{cell}</t></si>" for cell in strings)
    body = "".join(
        "<row>" + "".join(f'<c t="s"><v>{strings.index(cell)}</v></c>' for cell in row) + "</row>"
        for row in rows
    )
    namespace = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", f"<sst {namespace}>{shared}</sst>")
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet {namespace}><sheetData>{body}</sheetData></worksheet>",
        )
    return buffer.getvalue()


HEADER = ["te", "ab", "cd", "ef", "hij", "k", "kaz_name", "rus_name", "nn"]


def test_the_workbook_parser_resolves_shared_strings() -> None:
    sheet = read_sheet(
        _workbook([HEADER, ["750000000", "75", "00", "00", "000", "1", "a", "b", ""]])
    )
    assert sheet[0] == HEADER
    assert sheet[1][0] == "750000000"


def test_an_unexpected_header_stops_the_fetch_rather_than_guessing_columns() -> None:
    with pytest.raises(SystemExit):
        to_rows(read_sheet(_workbook([["code", "name"], ["750000000", "x"]])))


def test_the_spine_rows_are_sorted_so_a_rerun_produces_no_diff() -> None:
    sheet = [
        HEADER,
        ["750000000", "75", "00", "00", "000", "1", "Алматы қ.", "г.Алматы", ""],
        ["100000000", "10", "00", "00", "000", "0", "Абай облысы", "область Абай", ""],
    ]
    rows = to_rows(read_sheet(_workbook(sheet)))
    assert [row["kato"] for row in rows] == ["100000000", "750000000"]
    assert rows[0]["name_ru"] == "область Абай"


def test_a_duplicated_code_stops_the_fetch() -> None:
    """Deduplicating silently would hide a change in the source we must look at."""
    row = ["100000000", "10", "00", "00", "000", "0", "a", "b", ""]
    with pytest.raises(SystemExit):
        to_rows(read_sheet(_workbook([HEADER, row, [*row]])))


def test_the_level_rule_reads_the_code_not_the_type_digit() -> None:
    assert level_of("100000000") == "oblast"  # область Абай
    assert level_of("750000000") == "oblast"  # г.Алматы, a city, same level
    assert level_of("101000000") == "district"  # Семей Г.А.
    assert level_of("101010000") == "sub_district"  # г.Семей — a city, not a rural okrug
    assert level_of("103230100") == "settlement"  # с.Карааул


def test_the_type_code_legend_is_the_published_one() -> None:
    """НК РК 11-2025 §6.5. Five values; k is тип местности, not a level."""
    assert sorted(TYPE_CODE_LEGEND) == ["0", "1", "2", "3", "4"]
    assert TYPE_CODE_LEGEND["2"] == "сельская местность"


def test_the_workbook_link_is_found_by_name_and_never_guessed() -> None:
    """The returned URL must be percent-encoded.

    The site writes the Cyrillic filename unencoded in the href, and urllib
    encodes a request line as ASCII — so an unquoted path raises
    UnicodeEncodeError rather than any HTTP error, which reads as a bug in the
    parser rather than in the URL. Observed on the first live run.
    """
    found = find_workbook_url('<a href="/upload/iblock/e8/КАТО_17.07.2026.xlsx">x</a>')
    assert found == "https://stat.gov.kz/upload/iblock/e8/%D0%9A%D0%90%D0%A2%D0%9E_17.07.2026.xlsx"
    assert found.isascii()
    with pytest.raises(SystemExit):
        find_workbook_url('<a href="/upload/iblock/e8/ОКЭД.xlsx">x</a>')
    with pytest.raises(SystemExit):
        find_workbook_url(
            '<a href="/upload/a/КАТО_1.xlsx"></a><a href="/upload/b/КАТО_2.xlsx"></a>'
        )


def _write_targets(source: str) -> list[str]:
    """Every expression a script opens for writing, as written in the source."""
    targets: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        )

        if name in {"write_text", "write_bytes"} and isinstance(node.func, ast.Attribute):
            targets.append(ast.get_source_segment(source, node.func.value) or "")
        elif name == "open":
            # Two spellings: open(path, mode) as a builtin, path.open(mode) as a
            # method. They differ in whether the path is the first argument or the
            # attribute's owner, so resolve both to (target, positional modes).
            if isinstance(node.func, ast.Attribute):
                target: ast.expr = node.func.value
                positional = node.args
            elif node.args:
                target = node.args[0]
                positional = node.args[1:]
            else:
                continue

            mode = "r"
            for keyword in node.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = str(keyword.value.value)
            # Bound to a name because mypy does not narrow an indexed expression.
            first = positional[0] if positional else None
            if isinstance(first, ast.Constant):
                mode = str(first.value)

            if any(flag in mode for flag in "wax"):
                targets.append(ast.get_source_segment(source, target) or "")
    return targets


def test_only_publish_rates_writes_to_the_rates_csv() -> None:
    """`scripts/publish_rates.py` is the dataset's one deliberate writer.

    Nobody hand-confirms a row any more, so the earlier blanket ban on any
    script writing data/rates.csv is retired — but every OTHER script must
    still refuse to touch it. Walks the AST rather than grepping for two
    spellings, because a substring check passed for `open("data/rates.csv",
    "w")` while reading as stronger than it was.

    What it still cannot catch: a script that shells out, or one that builds
    the path at runtime from parts. The reviewer's diff read is what covers
    those, which is why data/rates.csv is the first file to open in any diff.
    """
    for path in Path(REPO_ROOT / "scripts").glob("*.py"):
        if path.stem == "publish_rates":
            continue
        for target in _write_targets(path.read_text(encoding="utf-8")):
            assert "RATES_CSV" not in target, f"{path.name} writes to the rates CSV"
            assert "rates.csv" not in target, f"{path.name} writes to the rates CSV"


def test_the_guard_above_actually_fires() -> None:
    """A check that has only run in the quiet case is unopposed, not tested."""
    assert _write_targets('RATES_CSV.open("w")') == ["RATES_CSV"]
    assert _write_targets('open("data/rates.csv", "w")') == ['"data/rates.csv"']
    assert _write_targets('open("data/rates.csv", mode="a")') == ['"data/rates.csv"']
    assert _write_targets("RATES_CSV.write_text(x)") == ["RATES_CSV"]
    assert _write_targets('RATES_CSV.open("r")') == []


# ---------------------------------------------------------------------------
# publish_rates.py — the dataset's only writer. What actually protects a row
# now that nobody hand-confirms it: every published row cites its source and
# stays inside the statutory band, no rate value is ever typed by this script
# (it all comes from data/mapped-rates.json), and КАТО codes stay strings.
# The cross-check that a published row is still what the mapper currently
# concludes lives above, in test_published_rows_match_the_currently_mapped_set_exactly.
# ---------------------------------------------------------------------------


def test_every_published_row_cites_a_source() -> None:
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            assert row["source_url"].strip(), row
            assert row["decision_ref"].strip(), row


def test_every_published_rate_is_inside_the_statutory_band() -> None:
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rate = float(row["rate"])
            assert RATE_MIN <= rate <= RATE_MAX, row


def test_every_published_kato_is_a_nine_digit_string() -> None:
    """A code parsed as an integer anywhere upstream would have already lost a
    leading zero by the time it reached the CSV; this is what would catch it."""
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            assert isinstance(row["kato"], str)
            assert KATO_RE.match(row["kato"]), row["kato"]


def test_publish_rates_takes_every_value_from_mapped_rates_json() -> None:
    """rows_from_mapped() must not invent a rate, a citation or a name: every
    field traces back to the mapper's own output (or, for the two fields it
    does not carry, to data/kato.csv and extract_rates.BASE_RATE_PERCENT)."""
    mapped = json.loads(MAPPED_RATES_JSON.read_text(encoding="utf-8"))
    mapped_rates_by_kato = {
        row["kato"]: round(float(row["rate"]), 4)
        for row in mapped["rows"]
        if row.get("outcome") == "mapped"
    }

    for row in publish_rates.rows_from_mapped():
        assert round(float(row["rate"]), 4) == mapped_rates_by_kato[row["kato"]]


def test_publish_rates_is_byte_stable_on_an_unchanged_rerun(tmp_path: Path) -> None:
    """Regenerating from an unchanged data/mapped-rates.json must reproduce the
    same bytes, so a rebuild with no upstream change leaves no diff."""
    first = tmp_path / "rates-a.csv"
    second = tmp_path / "rates-b.csv"
    publish_rates.write(publish_rates.rows_from_mapped(), first)
    publish_rates.write(publish_rates.rows_from_mapped(), second)
    assert first.read_bytes() == second.read_bytes()


def test_publish_rates_reproduces_the_committed_rates_csv(tmp_path: Path) -> None:
    """The committed data/rates.csv must be exactly what publish_rates.py would
    write today — otherwise the file on disk and its generator have drifted."""
    regenerated = tmp_path / "rates.csv"
    publish_rates.write(publish_rates.rows_from_mapped(), regenerated)
    assert regenerated.read_bytes() == RATES_CSV.read_bytes()
