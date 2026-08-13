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
    district_in_oblast,
    districts_by_oblast,
    jurisdiction_from_maslikhat,
    jurisdiction_from_title,
    map_row,
    oblast_codes,
    oblast_from_body,
    oblast_from_text,
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
    """«города Костаная» against «Костанай Г.А.» and «Костанайский район».

    The title says city and the citation names no kind of maslikhat, so only
    one of the two sources speaks — and one source cannot settle a
    jurisdiction any more than one reader can settle a rate.
    """
    text = "Решение маслихата города Костаная Костанайской области"
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
    """A title naming a city and a citation naming a районный маслихат."""
    outcome, _ = district_in_oblast(
        "Решение маслихата города Костаная Костанайского районного маслихата",
        "390000000",
        GROUPED,
        OBLASTS["390000000"],
    )
    assert outcome == JURISDICTION_DISAGREEMENT

    # And the independence that makes the disagreement meaningful: the title
    # pattern must not read the maslikhat's own adjective.
    assert jurisdiction_from_title("Костанайского районного маслихата") is None
