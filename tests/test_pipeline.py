"""The end-to-end path, run against the real files.

Every fixture row here is invented and lives only in this file. No test writes
to data/rates.csv, because a rate that no human read out of a decision must
never reach the dataset (SPEC.md §12).
"""

from __future__ import annotations

import ast
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from build import build, datapackage, read_aliases, render_index, render_llms_txt
from fetch_kato import FIELDS as KATO_CSV_FIELDS
from fetch_kato import TYPE_CODE_LEGEND, find_workbook_url, level_of, read_sheet, to_rows
from validate import (
    FIELDS,
    KATO_RE,
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
    "verified_by": "fixture",
    "verified_at": "2026-08-12",
}


def test_the_real_csv_has_the_agreed_header() -> None:
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        assert next(csv.reader(handle)) == FIELDS


def test_the_real_csv_validates() -> None:
    assert validate_file() == []


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


def test_the_page_inlines_the_data_and_requests_nothing() -> None:
    """SPEC.md §8.1: one self-contained file, no framework.

    A page that fetched rates.json would show an empty table under file:// —
    indistinguishable from a complete table of nothing.
    """
    page = render_index(build([VALID]))
    assert VALID["kato"] in page
    assert not re.findall(r'(?:src|href)="(?!#)[^"]+"', page)
    assert "<script src" not in page


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


# Anything that would make the browser open a connection: a stylesheet or
# preload link, a script with a src, a CSS import, a font declaration, or any
# absolute URL sitting in an attribute or a CSS url(). A source_url inside the
# JSON payload is none of these — it is data the page prints as a link, and it
# is the whole point of the project.
EXTERNAL = (
    r"<link\b",
    r"<script[^>]*\bsrc\s*=",
    r"@import",
    r"@font-face",
    r"url\(\s*['\"]?https?:",
    r"(?:src|href)\s*=\s*['\"]\s*(?:https?:)?//",
)


def test_the_built_page_references_no_external_script_style_or_font() -> None:
    """The invariant that had no guard, which is how it would quietly regress.

    MiniSearch is vendored and inlined for exactly this reason. A CDN tag would
    look fine on a laptop with a network and would leave the page with no search
    for the reader who has none, and offline is the case this page is built for.
    """
    page = render_index(build([VALID]))
    for pattern in EXTERNAL:
        assert not re.search(pattern, page, re.IGNORECASE), pattern
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


def test_llms_txt_says_the_urls_do_not_fetch_while_the_repository_is_private() -> None:
    """A URL that 404s reads as a fact, whether it is invented or merely private.

    The placeholder went away when the remote was created; the warning must not
    have gone with it. A private repository serves no Pages and no jsDelivr.
    """
    text = render_llms_txt(build([]))
    assert "NOT FETCHABLE TODAY" in text
    assert "PRIVATE repository" in text
    assert "<user>" not in text


def test_the_jsdelivr_url_names_the_branch_this_repository_actually_uses() -> None:
    """SPEC.md §8.2 writes @main. The default branch is master, and jsDelivr is literal."""
    text = render_llms_txt(build([]))
    assert "kz-tax-rates@master/dist/rates.json" in text
    assert "@main/" not in text


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


def test_no_python_file_writes_to_the_rates_csv() -> None:
    """Only a human edits data/rates.csv. A script that opens it for writing is the bug.

    Walks the AST rather than grepping for two spellings, because the earlier
    substring version passed for `open("data/rates.csv", "w")` — a guard that
    read as stronger than it was.

    What it still cannot catch: a script that shells out, or one that builds the
    path at runtime from parts. The reviewer's diff read is what covers those,
    which is why data/rates.csv is the first file to open in any diff — a
    one-line change there is easy to miss inside a large one.
    """
    for path in Path(REPO_ROOT / "scripts").glob("*.py"):
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
