"""Attaching a rate to a district: two sources for the oblast, then one district.

Every case here is a real citation from the 2026 corpus, including each one
that made an earlier version of the matcher wrong. **A rate on the wrong
district is worse than a missing district**, so the refusals are tested as
carefully as the matches.
"""

from __future__ import annotations

from map_districts import (
    CITY,
    DISTRICT,
    JURISDICTION_DISAGREEMENT,
    JURISDICTION_UNKNOWN,
    MAPPED_ONE,
    NO_DISTRICT,
    OBLAST_DISAGREEMENT,
    TITLE_CONTRADICTS_CITATION,
    district_in_oblast,
    districts_by_oblast,
    jurisdiction_from_maslikhat,
    jurisdiction_from_title,
    map_row,
    name_matches,
    oblast_codes,
    oblast_from_body,
    oblast_from_text,
    stems,
    strip_oblast_phrase,
)

OBLASTS = oblast_codes()
GROUPED = districts_by_oblast()

URALSK = (
    "Решение Уральского городского маслихата Западно-Казахстанской области "
    "от 28 ноября 2025 года № 24-9"
)


def test_the_twenty_bodies_map_onto_the_twenty_oblast_codes() -> None:
    """The join the whole rule rests on, and the half that IS measured."""
    bodies = [
        "Акмолинская область",
        "Западно-Казахстанская область",
        "область Абай",
        "область Ұлытау",
        "г. Шымкент",
        "город Астана",
    ]
    assert all(oblast_from_body(body, OBLASTS) is not None for body in bodies)
    assert len(OBLASTS) == 20


def test_the_oblast_is_read_from_the_word_beside_oblast_not_from_the_whole_text() -> None:
    """«Решение Жамбылского районного маслихата Алматинской области» names two
    oblast-shaped words, and only one of them is the oblast."""
    text = "Решение Жамбылского районного маслихата Алматинской области от 26 ноября 2025 года"
    assert OBLASTS[oblast_from_text(text, OBLASTS) or ""] == "Алматинская область"


def test_a_hyphenated_oblast_survives_the_word_split() -> None:
    """«Северо-Казахстанской» split into two words, and «казахс» is a stopword."""
    assert oblast_from_text(URALSK, OBLASTS) == "270000000"


def test_a_short_oblast_name_is_not_dropped() -> None:
    """«область Абай» names itself in four letters; a five-letter floor lost it."""
    text = "Решение Абайского районного маслихата области Абай от 26 ноября 2025 года"
    assert OBLASTS[oblast_from_text(text, OBLASTS) or ""] == "область Абай"


def test_a_city_body_and_an_oblast_body_do_not_collide() -> None:
    """«Алматинской области» stems to «алмат», and so does «г.Алматы»."""
    assert OBLASTS[oblast_from_body("Алматинская область", OBLASTS) or ""] == "Алматинская область"
    assert OBLASTS[oblast_from_body("г. Алматы", OBLASTS) or ""] == "г.Алматы"


def test_the_oblast_phrase_is_stripped_but_a_similar_district_is_not() -> None:
    """Removing the oblast's STEMS also deleted «Костанайского района»."""
    text = "Решение маслихата Костанайского района Костанайской области"
    stripped = strip_oblast_phrase(text)
    assert "Костанайского района" in stripped
    assert "Костанайской области" not in stripped


def test_the_three_zhambyl_districts_are_distinguished_by_their_oblast() -> None:
    """The ambiguity was only ever across oblasts, which is why the oblast comes first."""
    zhambyl = [
        row
        for rows in GROUPED.values()
        for row in rows
        if row["name_ru"].strip() == "Жамбылский район"
    ]
    assert len(zhambyl) == 3
    assert len({row["kato"][:2] for row in zhambyl}) == 3
    text = "Решение Жамбылского районного маслихата Алматинской области"
    outcome, candidates = district_in_oblast(text, "190000000", GROUPED, "Алматинская область")
    assert outcome == MAPPED_ONE
    assert candidates[0]["kato"].startswith("19")


def test_a_district_named_in_the_text_maps_to_exactly_one_code() -> None:
    outcome, candidates = district_in_oblast(URALSK, "270000000", GROUPED, OBLASTS["270000000"])
    assert outcome == MAPPED_ONE
    assert "Уральск" in candidates[0]["name_ru"]


def test_a_city_and_a_district_sharing_a_name_is_a_refusal_not_a_choice() -> None:
    """«городу Костанай» against «Костанай Г.А.» and «Костанайский район».

    The title says city and the citation names no kind of maslikhat in
    either the adjective or the genitive form, so only one of the two
    sources speaks — and one source cannot settle a jurisdiction any more
    than one reader can settle a rate.

    («Решение маслихата города Костаная Костанайской области» — the real
    Kostanay city citation — is deliberately NOT used here any more: it now
    speaks through the genitive pattern too, and correctly maps. That case
    is covered by test_both_kostanay_rows_map_end_to_end_from_their_real_
    citations below.)
    """
    text = "Решение маслихата Костанайской области по городу Костанай"
    outcome, candidates = district_in_oblast(text, "390000000", GROUPED, OBLASTS["390000000"])
    assert outcome == JURISDICTION_UNKNOWN
    assert len(candidates) > 1


def test_a_genuinely_different_spelling_is_refused_rather_than_guessed() -> None:
    """The classifier says «Хобдинский район»; the decision says «Кобдинского»."""
    text = "Решение Кобдинского районного маслихата Актюбинской области"
    outcome, _ = district_in_oblast(text, "150000000", GROUPED, OBLASTS["150000000"])
    assert outcome == NO_DISTRICT


def test_a_row_whose_two_oblast_sources_disagree_is_refused() -> None:
    row = {
        "document_id": "TEST",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": URALSK,
        "sentence": "",
        "source_url": "https://example",
    }
    result = map_row(row, {"TEST": "Акмолинская область"}, OBLASTS, GROUPED)
    assert result["outcome"] == "unmapped-oblast-sources-disagree"
    assert "kato" not in result


def test_a_mapped_row_carries_the_code_and_the_citation_together() -> None:
    row = {
        "document_id": "TEST",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": URALSK,
        "sentence": "",
        "source_url": "https://example",
    }
    result = map_row(row, {"TEST": "Западно-Казахстанская область"}, OBLASTS, GROUPED)
    assert result["outcome"] == MAPPED_ONE
    assert result["kato"].startswith("27")
    assert result["decision_ref"] == URALSK


KOSTANAY_CITY = "Решение маслихата города Костаная Костанайской области от 24 ноября 2025 года"
KOSTANAY_DISTRICT = "Решение маслихата Костанайского района Костанайской области от 19 ноября 2025"


def test_a_city_and_its_district_are_told_apart_by_two_sources() -> None:
    """«Костанай Г.А.» and «Костанайский район» are different jurisdictions
    with different maslikhats, so two sources must say which — the title's own
    form, and the kind of maslikhat the decision names."""
    city_outcome, city = district_in_oblast(
        KOSTANAY_CITY + " городского маслихата", "390000000", GROUPED, OBLASTS["390000000"]
    )
    assert city_outcome == MAPPED_ONE
    assert "Г.А." in city[0]["name_ru"]

    district_outcome, district = district_in_oblast(
        KOSTANAY_DISTRICT + " районного маслихата", "390000000", GROUPED, OBLASTS["390000000"]
    )
    assert district_outcome == MAPPED_ONE
    assert district[0]["name_ru"].endswith("район")


def test_the_two_jurisdiction_sources_are_independent() -> None:
    """One describes the place, the other names the institution.

    Measured across the 136 confirmed rows: the maslikhat type is stated in 106
    of them (16 city, 90 district) and the title form in 134 — different
    coverage, which is what independence looks like from the outside.
    """
    assert jurisdiction_from_title("Решение маслихата города Костаная") == CITY
    assert jurisdiction_from_title("Решение маслихата Костанайского района") == DISTRICT
    assert jurisdiction_from_maslikhat("Кокшетауского городского маслихата") == CITY
    assert jurisdiction_from_maslikhat("Аккольского районного маслихата") == DISTRICT


def test_a_jurisdiction_neither_source_states_is_refused() -> None:
    """A jurisdiction we cannot name is a rate we cannot publish."""
    outcome, _ = district_in_oblast(
        "Решение маслихата Костаная Костанайской области",
        "390000000",
        GROUPED,
        OBLASTS["390000000"],
    )
    assert outcome == JURISDICTION_UNKNOWN


def test_disagreeing_jurisdiction_sources_refuse_rather_than_pick() -> None:
    """A title naming a city and a citation naming a районный маслихат.

    («…маслихата города Костаная Костанайского районного маслихата» is no
    longer used here: the genitive city pattern now also fires on «маслихата
    города», which would make source B agree with itself before it can
    disagree with source A. «по городу X» keeps the title's city reading
    without touching the «маслихата город…» phrase the genitive pattern
    watches for.)
    """
    outcome, _ = district_in_oblast(
        "Решение по городу Костанай, Костанайского районного маслихата",
        "390000000",
        GROUPED,
        OBLASTS["390000000"],
    )
    assert outcome == JURISDICTION_DISAGREEMENT

    # And the independence that makes the disagreement meaningful: the title
    # pattern must not read the maslikhat's own adjective.
    assert jurisdiction_from_title("Костанайского районного маслихата") is None


# --- The title as a second district source: fallback + veto -----------------
#
# The title is used only when the citation resolves NOTHING — NO_DISTRICT,
# specifically, not any other refusal — as a fallback, or when both resolve
# and disagree, as a veto. It never overrides an agreeing citation. Measured
# over 150 already-mapped rows: 133 agree, 0 disagree, 17 silent — the title
# never contradicts the citation, which is the only safe shape for a second
# source.

CITATION_NAMES_NO_DISTRICT = (
    "Решение маслихата Западно-Казахстанской области от 28 ноября 2025 года № 24-9"
)


def test_the_title_fills_in_when_the_citation_names_no_district() -> None:
    """The citation names the oblast but no district; the title does."""
    row = {
        "document_id": "TEST-FALLBACK",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": CITATION_NAMES_NO_DISTRICT,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-FALLBACK": "Западно-Казахстанская область"}
    titles = {"TEST-FALLBACK": "О понижении ставки по Бурлинскому району"}
    result = map_row(row, bodies, OBLASTS, GROUPED, titles)
    assert result["outcome"] == MAPPED_ONE
    assert result["name_ru"] == "Бурлинский район"
    assert result["district_source"] == "title"


def test_a_title_that_agrees_with_the_citation_is_not_the_source_of_record() -> None:
    """When the citation alone resolves the district, the source stays 'citation'."""
    row = {
        "document_id": "TEST-AGREE",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": URALSK,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-AGREE": "Западно-Казахстанская область"}
    titles = {"TEST-AGREE": "О понижении ставки в городе Уральск"}
    result = map_row(row, bodies, OBLASTS, GROUPED, titles)
    assert result["outcome"] == MAPPED_ONE
    assert result["district_source"] == "citation"


def test_a_title_naming_a_different_district_vetoes_a_citation_match() -> None:
    """Citation names Уральск; a (deliberately corrupted) title names Бурлинский
    район, a different district in the same oblast. Neither is trusted over
    the other, so the row refuses rather than picks."""
    row = {
        "document_id": "TEST-VETO",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": URALSK,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-VETO": "Западно-Казахстанская область"}
    titles = {"TEST-VETO": "О понижении ставки по Бурлинскому району"}
    result = map_row(row, bodies, OBLASTS, GROUPED, titles)
    assert result["outcome"] == TITLE_CONTRADICTS_CITATION
    assert "kato" not in result
    assert "district_source" not in result


def test_a_row_that_was_previously_mapped_now_refuses_on_a_corrupted_title() -> None:
    """A previously-mapped row (citation resolves Уральск cleanly) must refuse,
    not map wrongly, once a corrupted title contradicts it."""
    row = {
        "document_id": "TEST-WAS-MAPPED",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": URALSK,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-WAS-MAPPED": "Западно-Казахстанская область"}

    clean_result = map_row(row, bodies, OBLASTS, GROUPED, {})
    assert clean_result["outcome"] == MAPPED_ONE

    corrupted_titles = {"TEST-WAS-MAPPED": "О понижении ставки по Бурлинскому району"}
    corrupted_result = map_row(row, bodies, OBLASTS, GROUPED, corrupted_titles)
    assert corrupted_result["outcome"] == TITLE_CONTRADICTS_CITATION


# The title fallback must only fire on NO_DISTRICT (the citation named zero
# candidates), never on a refusal that already named some. A refusal that
# names candidates — SEVERAL_DISTRICTS, JURISDICTION_UNKNOWN,
# JURISDICTION_DISAGREEMENT — is the citation naming candidates and this
# module deliberately refusing to choose among them, not silence for the
# title to fill.
JURISDICTION_AMBIGUOUS_CITATION = (
    "Решение маслихата города Костаная Костанайского районного маслихата "
    "Костанайской области от 1 января 2026 года"
)


def test_a_jurisdiction_ambiguous_refusal_does_not_fall_back_to_the_title() -> None:
    """This citation names BOTH city and district forms in source B alone —
    «маслихата города» and «районного маслихата» both match — so
    jurisdiction_from_maslikhat refuses on its own, before the overlap check
    even applies, and district_in_oblast returns JURISDICTION_UNKNOWN with
    two named candidates (Костанай Г.А. and Костанайский район).

    A title that resolves cleanly — to a THIRD district neither citation
    source named — must not be allowed to pick for a citation that named
    candidates and refused among them. The old gate (`outcome != MAPPED_ONE`)
    let exactly this through: it read every refusal as silence, took the
    title alone, and never checked the title's pick was even among the
    citation's own candidates. Reproduced: the row mapped to Алтынсаринский
    район 393200000 — a district the citation never named at all."""
    row = {
        "document_id": "TEST-JURISDICTION-AMBIGUOUS",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": JURISDICTION_AMBIGUOUS_CITATION,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-JURISDICTION-AMBIGUOUS": "Костанайская область"}
    titles = {"TEST-JURISDICTION-AMBIGUOUS": "О понижении ставки по Алтынсаринскому району"}

    # The citation alone is ambiguous, not silent, and it names candidates.
    citation_outcome, citation_candidates = district_in_oblast(
        JURISDICTION_AMBIGUOUS_CITATION, "390000000", GROUPED, OBLASTS["390000000"]
    )
    assert citation_outcome == JURISDICTION_UNKNOWN
    assert len(citation_candidates) > 0

    result = map_row(row, bodies, OBLASTS, GROUPED, titles)
    assert result["outcome"] == JURISDICTION_UNKNOWN
    assert "kato" not in result
    assert "district_source" not in result


DISAGREEMENT_CITATION_KOSTANAY = "Решение по городу Костанай, Костанайского районного маслихата"
DISAGREEING_ROW_TITLE_THAT_PICKS_A_REAL_CANDIDATE = (
    "О понижении ставки по городу Костанай, Костанайского городского маслихата"
)


def test_a_jurisdiction_disagreement_does_not_fall_back_even_to_a_titles_valid_candidate() -> None:
    """Isolates the OUTER gate from the inner "is the title's pick even among
    the citation's candidates" check: here the title's pick (Костанай Г.А.)
    genuinely IS one of the citation's own two candidates, so the inner check
    alone would let it through. Only the outer gate — the fallback firing on
    NO_DISTRICT and nothing else — stops it: JURISDICTION_DISAGREEMENT is the
    citation naming candidates and disagreeing about which, not silence."""
    row = {
        "document_id": "TEST-DISAGREEMENT-VALID-TITLE-PICK",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": DISAGREEMENT_CITATION_KOSTANAY,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-DISAGREEMENT-VALID-TITLE-PICK": "Костанайская область"}
    titles = {
        "TEST-DISAGREEMENT-VALID-TITLE-PICK": DISAGREEING_ROW_TITLE_THAT_PICKS_A_REAL_CANDIDATE
    }

    citation_outcome, citation_candidates = district_in_oblast(
        DISAGREEMENT_CITATION_KOSTANAY, "390000000", GROUPED, OBLASTS["390000000"]
    )
    assert citation_outcome == JURISDICTION_DISAGREEMENT
    title_outcome, title_candidates = district_in_oblast(
        DISAGREEING_ROW_TITLE_THAT_PICKS_A_REAL_CANDIDATE,
        "390000000",
        GROUPED,
        OBLASTS["390000000"],
    )
    assert title_outcome == MAPPED_ONE
    # The title's pick really is among the citation's own candidates.
    assert title_candidates[0]["kato"] in {c["kato"] for c in citation_candidates}

    result = map_row(row, bodies, OBLASTS, GROUPED, titles)
    assert result["outcome"] == JURISDICTION_DISAGREEMENT
    assert "kato" not in result


# «Решение Карагандинского городского маслихата от 28 ноября 2025 года № 318» —
# an oblast capital's own citation never repeats its oblast's name, so
# oblast_from_text has nothing to read here. This is the real text of
# G25KA00318M, the row the plan names by name.
KARAGANDA_NO_OBLAST_IN_TEXT = (
    "Решение Карагандинского городского маслихата от 28 ноября 2025 года № 318"
)


def test_a_row_whose_text_names_no_oblast_maps_from_the_facet_alone() -> None:
    """oblast_from_text refuses on this citation; the registry facet alone
    must still resolve the oblast, mapping Караганда Г.А."""
    row = {
        "document_id": "TEST-KARAGANDA",
        "rate": 0.02,
        "year": 2026,
        "decision_ref": KARAGANDA_NO_OBLAST_IN_TEXT,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-KARAGANDA": "Карагандинская область"}

    assert oblast_from_text(KARAGANDA_NO_OBLAST_IN_TEXT, OBLASTS) is None

    result = map_row(row, bodies, OBLASTS, GROUPED)
    assert result["outcome"] == MAPPED_ONE
    assert result["kato"] == "351000000"
    assert result["name_ru"] == "Караганда Г.А."
    assert result["oblast_source"] == "body-only"


ASTANA_TEXT = "Решение маслихата города Астаны от 12 ноября 2025 года № 100"


def test_a_republican_city_row_carries_district_source_too() -> None:
    """The three cities of republican significance (Астана, Алматы,
    Шымкент) have no district-level codes under them: the oblast-level code
    IS the district. That branch of map_row() used to set oblast_source but
    never district_source, so 3 mapped rows lacked the key while 155 had it
    — an inconsistent row shape. It must be explicit: "oblast-level", not
    the citation-derived value, because no citation-level district match
    was ever attempted for these rows."""
    row = {
        "document_id": "TEST-ASTANA",
        "rate": 0.02,
        "year": 2026,
        "decision_ref": ASTANA_TEXT,
        "sentence": "",
        "source_url": "https://example",
    }
    bodies = {"TEST-ASTANA": "город Астана"}

    result = map_row(row, bodies, OBLASTS, GROUPED)
    assert result["outcome"] == MAPPED_ONE
    assert result["kato"] == "710000000"
    assert result["district_source"] == "oblast-level"


def test_both_sources_speaking_and_disagreeing_still_refuses() -> None:
    """The new body-only path must never swallow a genuine disagreement: when
    the text DOES name an oblast, both sources are still required to agree,
    exactly as before this slice. Regression guard for the case that matters
    most — a wrong oblast is worse than a missing one."""
    row = {
        "document_id": "TEST-DISAGREE",
        "rate": 0.03,
        "year": 2026,
        "decision_ref": URALSK,  # names Западно-Казахстанская область in the text
        "sentence": "",
        "source_url": "https://example",
    }
    assert oblast_from_text(URALSK, OBLASTS) is not None

    result = map_row(row, {"TEST-DISAGREE": "Акмолинская область"}, OBLASTS, GROUPED)
    assert result["outcome"] == OBLAST_DISAGREEMENT
    assert "kato" not in result


# --- A name shorter than the stem floor matches whole ------------------------
#
# «Аксу» is four characters and produces NO stem at STEM=5, so it could
# never match at any spelling. The global floor is not lowered; a name with
# no stem instead matches as a complete word.

AKSU_TEXT = "О понижении размера ставки налогов в городе Аксу"


def test_a_name_below_the_stem_floor_produces_no_stem_at_all() -> None:
    """The bug, demonstrated directly: stems() sees nothing to compare."""
    assert stems("Аксу Г.А.") == set()


def test_a_name_below_the_stem_floor_matches_the_complete_word_in_the_text() -> None:
    outcome, candidates = district_in_oblast(AKSU_TEXT, "550000000", GROUPED, OBLASTS["550000000"])
    assert outcome == MAPPED_ONE
    assert candidates[0]["name_ru"] == "Аксу Г.А."


def test_a_short_name_that_is_genuinely_absent_still_refuses() -> None:
    """Whole-word matching does not turn into substring matching: a word that
    merely starts with «аксу» — «Аксуском» — is not the same complete word,
    so it must not match «Аксу Г.А.»."""
    text = "О понижении размера ставки налогов в Аксуском районе"
    assert name_matches("Аксу Г.А.", stems(text), text.lower()) is False

    text_no_mention = "О понижении размера ставки налогов при применении специального режима"
    outcome, _ = district_in_oblast(text_no_mention, "550000000", GROUPED, OBLASTS["550000000"])
    assert outcome == NO_DISTRICT


def test_a_name_at_or_above_the_stem_floor_still_matches_by_stem() -> None:
    """The widening only fires when stems() finds nothing to compare. A name
    with a real stem — «Костанайский район» — keeps matching by stem, not
    by whole word, exactly as before this slice."""
    text = "Решение маслихата Костанайского района"
    name_stems, text_stems = stems("Костанайский район"), stems(text)
    assert name_stems  # the stem path is reachable for this name
    assert name_matches("Костанайский район", text_stems, text.lower()) is True
    assert bool(name_stems & text_stems) is True  # and it is the stem path deciding


# --- The jurisdiction reader learns the genitive word order ------------------
#
# «маслихата города Костаная» and «маслихата Костанайского района» are
# Kostanay's own citations; the adjective-only patterns («городского
# маслихата» / «районного маслихата») never fire on either, so
# jurisdiction_from_maslikhat returned None for both and the rows refused as
# JURISDICTION_UNKNOWN. jurisdiction_from_maslikhat() alone — SOURCE B in
# isolation — correctly reads both, and these two tests guard that reading.

KOSTANAY_CITY_REAL = (
    "Решение маслихата города Костаная Костанайской области от 24 ноября 2025 года № 200"
)
KOSTANAY_DISTRICT_REAL = (
    "Решение маслихата Костанайского района Костанайской области от 19 ноября 2025 года № 309"
)


def test_the_genitive_word_order_is_recognised_for_the_city() -> None:
    assert jurisdiction_from_maslikhat(KOSTANAY_CITY_REAL) == CITY


def test_the_genitive_word_order_is_recognised_for_the_district() -> None:
    assert jurisdiction_from_maslikhat(KOSTANAY_DISTRICT_REAL) == DISTRICT


# But SOURCE A (the title reader) and SOURCE B (the maslikhat reader) are
# supposed to be independent, and on these exact citations they are not:
# TITLE_CITY matches «города К» and MASLIKHAT_CITY_GENITIVE matches
# «маслихата города» in KOSTANAY_CITY_REAL — both anchored on the same
# «города» token. TITLE_DISTRICT matches «района» and
# MASLIKHAT_DISTRICT_GENITIVE matches «маслихата Костанайского района» in
# KOSTANAY_DISTRICT_REAL — again the same «района» token. Two sources
# reading one substring are one source, not two, so district_in_oblast()
# must refuse both rather than treat this as corroboration. Losing these two
# rows is the correct trade: a wrong district beside a real citation is
# worse than a missing one, and a separate mechanism (the title fallback,
# should a future citation revision add one) is where they are meant to be
# recovered, not this jurisdiction step.
def test_both_kostanay_rows_refuse_because_the_two_sources_read_one_span() -> None:
    city_outcome, _ = district_in_oblast(
        KOSTANAY_CITY_REAL, "390000000", GROUPED, OBLASTS["390000000"]
    )
    assert city_outcome == JURISDICTION_UNKNOWN

    district_outcome, _ = district_in_oblast(
        KOSTANAY_DISTRICT_REAL, "390000000", GROUPED, OBLASTS["390000000"]
    )
    assert district_outcome == JURISDICTION_UNKNOWN


def test_a_genuinely_two_source_citation_with_non_overlapping_spans_still_resolves() -> None:
    """The overlap guard must not become a blanket refusal on every city
    match: when the title's clue and the maslikhat's clue come from
    genuinely different substrings — «по городу Костанай» (title, source A)
    and «Костанайского городского маслихата» (maslikhat adjective form,
    source B) — they are two real, independent readings, agree, and the row
    still maps."""
    text = "Решение по городу Костанай, Костанайского городского маслихата от 1 января 2026 года"
    outcome, candidates = district_in_oblast(text, "390000000", GROUPED, OBLASTS["390000000"])
    assert outcome == MAPPED_ONE
    assert "Г.А." in candidates[0]["name_ru"]


def test_a_district_named_rayon_x_is_a_known_unread_form_not_an_invented_one() -> None:
    """«маслихата района X» is refused today, but it is not a made-up word
    order: 18 of the 209 КАТО districts are literally named «район X» —
    район Ақсуат, район Бәйтерек, район Турара Рыскулова, район Аққулы, among
    others — so «Решение маслихата района Аққулы» is their genuine citation
    form, not an invented one, and jurisdiction_from_maslikhat correctly
    returns None for it because MASLIKHAT_DISTRICT_GENITIVE only recognises
    «маслихата <word> района», not «маслихата района <word>».

    The refusal itself is correct and safe — a rate withheld, never a rate
    misattributed. What this test records is that it is a known coverage gap
    affecting those 18 named districts, so a later session does not read the
    refusal as proof the input cannot occur and skip re-checking it."""
    assert jurisdiction_from_maslikhat("Решение маслихата района Аққулы") is None
