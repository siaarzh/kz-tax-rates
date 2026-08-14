"""The reader ensemble, against the real document text and against broken text.

`tests/fixtures/G25ZA00249M.txt` is the text of the real Уральск decision,
extracted from the PDF whose sha256 is
261b159ecae827598d56f3b9f685e30d63f69146ed0d2114427a2362629fcd44. Committed as
text so the suite exercises the real thing without a network call, and so a
future reader can see exactly what the parser was written against.

`tests/fixtures/G25PM14750M.txt` is the Щербактинский район decision, sha256 of
the PDF d208b840fba02d75a4237a93db0af587820c9af8c9334087bbe0f47b996579cb. It
reached `data/extraction-queue.json` as a `conflict` on 2026-08-12: it writes
`до` where the reference document writes `на`, so readers 1 and 3 found no
clause while reader 2, which does not anchor on a preposition, read the rate.

Every rate in this file is either read from that fixture or deliberately
corrupted to prove a refusal. None of it reaches data/rates.csv.

**The Щербактинский tests never name the rate they expect**, unlike the older
ones above them. A test that writes `== 0.03` asserts a number its author
supplied; these assert that the readers agree with each other and that the
number the parser produced is written in the sentence the parser kept. That
holds whatever the document says, so it cannot pass on a remembered answer.

`tests/fixtures/G25GA00334M.txt` (sha256
3902915b4efaeb9f00a565211202ffc8a85ee1750349728d92fb8e7f765edf89) and its
Kazakh copy `G25GA00334M.kaz.txt` (sha256
885c9dc167c23c67912b250fbe3f785019f7e60a97f6f8c6608fb1025e45ad7e) are Тараз
city's decision. It reached the queue as `readers disagree: [2, 4]`: reader 2
(the spelled word) read 4, reader 4 (Kazakh) read 2 — and it was reader 2 that
was wrong, not reader 4. The sentence writes `на 2 ( два) процента` with a
space right after the opening paren, which the word reader's pattern did not
tolerate, so it missed that parenthetical entirely and returned the OLD rate's
word (`четырех`) as if it were the last one in the sentence.

`tests/fixtures/G25GB00533M.txt` (sha256
cdcda5238ad46aed73dfec3b6d4c24a0e965d3bae488f6ab82d7e884f1e68ab9) and its
Kazakh copy `G25GB00533M.kaz.txt` (sha256
9d28ed44bba1b41b4bc9f46ec818b65dd57830ec2ff4f2c4c4393a39d8da0bf0) are
Байзакский район's decision. It states no year `read_year` or
`read_year_from_in_force` can read — its entry-into-force clause reads
"по истечении 10 календарных дней после … опубликования", a date that is not
knowable in advance — but the same sentence separately states
"распространяется на правоотношения, возникшие с 1 января 2026 года", which
is where `read_year_from_retroactive_effect` reads 2026 from.

`tests/fixtures/G25FK28198M.txt` (sha256
b331f8e4b7abe00076271858d515c2bafd4d1e8be2016aeffee7c5e19fff11b7) is Мақаншы
район's decision. Its own text misspells "вводится" as "вводиться" — a
document typo, not a parser one — and reached the queue because the in-force
reader's marker did not tolerate it.
"""

from __future__ import annotations

import email.message
import hashlib
import json
import time
import urllib.error
from pathlib import Path

import discover_decisions
import enumerate_decisions
import pytest
import validate_readings
from extract_rates import (
    CONFIRMED,
    CONFLICT,
    UNPARSED,
    classify,
    rate_sentence,
    read_digit,
    read_kazakh,
    read_regime,
    read_transition,
    read_word,
    read_year,
    read_year_from_in_force,
    read_year_from_retroactive_effect,
)


def _fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


REAL = _fixture("G25ZA00249M.txt")
DO_FORM = _fixture("G25PM14750M.txt")

# The same decisions as published in Kazakh — separate files, which is where
# reader 4's independence comes from. A transcription slip cannot cross a
# translation.
REAL_KAZ = _fixture("G25ZA00249M.kaz.txt")
DO_FORM_KAZ = _fixture("G25PM14750M.kaz.txt")

# The two the Russian readers cannot read: one states the rate once in words,
# the other only as `с 4% до 3%`. Their Kazakh copies state it plainly.
ONCE_ONLY_KAZ = _fixture("G25NN00309M.kaz.txt")
PERCENT_ONLY_KAZ = _fixture("G25NJ00343M.kaz.txt")

# The clause the readers work on, from the real document.
CLAUSE = "с 4 (четырех) процентов на 3 (три) процента"
# The same movement as the Щербактинский district writes it.
DO_CLAUSE = "с 4 (четырех) процентов до 3 (трех) процентов"

# Тараз — `readers disagree: [2, 4]` on the queue, and it was the word reader
# that was wrong: a space right after the opening paren, `( два)`, that its
# pattern did not tolerate.
SPACED_PAREN = _fixture("G25GA00334M.txt")
SPACED_PAREN_KAZ = _fixture("G25GA00334M.kaz.txt")

# Байзакский район — no year `read_year` or `read_year_from_in_force` can
# read; the year is stated only via "распространяется на правоотношения,
# возникшие с 1 января 2026 года".
RETROACTIVE_ONLY = _fixture("G25GB00533M.txt")
RETROACTIVE_ONLY_KAZ = _fixture("G25GB00533M.kaz.txt")

# Мақаншы район — "вводиться" (typo, extra ь) instead of "вводится".
IN_FORCE_TYPO = _fixture("G25FK28198M.txt")


def test_the_real_decision_is_confirmed_at_three_percent() -> None:
    result = classify(REAL, kazakh_text=REAL_KAZ)
    assert result["outcome"] == CONFIRMED
    assert result["rate"] == 0.03
    assert result["year"] == 2026
    assert result["in_force_from"] == "2026-01-01"
    assert "№ 24-9" in result["decision_ref"]


def test_the_confirmed_row_carries_the_sentence_it_was_read_from() -> None:
    """A reader must be able to check the row without trusting the parser."""
    assert CLAUSE in classify(REAL, kazakh_text=REAL_KAZ)["sentence"]


def test_all_four_readers_agree_on_the_real_document() -> None:
    """Three over the Russian text, one over the separately published Kazakh file.

    The fifth reading is `regime` — it never carries a rate, only an
    objection, and this document's sentence names no regime marker at all.
    """
    readings = classify(REAL, kazakh_text=REAL_KAZ)["readings"]
    assert [reading["rate_percent"] for reading in readings] == [3, 3, 3, 3, None]
    assert [reading["reader"] for reading in readings] == [
        "digit",
        "word",
        "transition",
        "kazakh",
        "regime",
    ]


def test_the_digit_reader_reads_digits_only() -> None:
    assert read_digit(CLAUSE).rate_percent == 3
    assert read_digit("с четырех процентов на три процента").rate_percent is None


def test_the_word_reader_reads_words_only_and_does_not_consult_the_digit() -> None:
    """Proven by disagreeing with the digit when the two are made to differ."""
    assert read_word(CLAUSE).rate_percent == 3
    assert read_word("с 4 (четырех) процентов на 3 (два) процента").rate_percent == 2
    assert read_word("с 4 процентов на 3 процента").rate_percent is None


def test_the_word_reader_tolerates_a_space_after_the_opening_paren() -> None:
    """G25GA00334M and five siblings: `на 2 ( два) процента` — a space `(` did not cross.

    Reached the queue as `readers disagree: [2, 4]`. It was reader 2 that was
    wrong: its pattern required the word to touch both parentheses, so it never
    saw `( два)` at all and returned the OLD rate's word — the last (and only)
    one it could find — while reader 4 (Kazakh) read the new rate correctly.
    """
    assert read_word("с 4 (четырех) процентов на 2 ( два) процента").rate_percent == 2
    assert read_word("с 4 (четырех) процентов на 2 (два ) процента").rate_percent == 2


def test_the_real_taraz_decision_confirms_at_two_percent() -> None:
    result = classify(SPACED_PAREN, kazakh_text=SPACED_PAREN_KAZ)
    readings = {r["reader"]: r["rate_percent"] for r in result["readings"]}
    assert readings["word"] == 2
    assert readings["kazakh"] == 2
    assert result["outcome"] == CONFIRMED
    assert result["rate"] == 0.02


def test_the_transition_reader_refuses_a_rise_and_a_wrong_starting_point() -> None:
    assert read_transition(CLAUSE, 2026).rate_percent == 3
    assert read_transition("с 3 (три) процентов на 4 (четыре) процента", 2026).rate_percent is None
    assert read_transition("с 5 (пяти) процентов на 3 (три) процента", 2026).rate_percent is None


# --- READER 5: which special regime the sentence governs --------------------
#
# Article 726 also sets the rate for розничный налог (retail tax), a separate
# special regime, in near-identical wording. Seven published districts were
# retail-tax decisions, read correctly by every reader above and published as
# if they were simplified-declaration rates — nothing checked which regime the
# sentence actually governed. `read_regime` is the fifth reader that does.
RETAIL_ONLY = (
    "Решение маслихата Тестового района от 20 ноября 2025 года № 1. "
    "Понизить размер ставки корпоративного или индивидуального подоходного налога, "
    "за исключением налогов, удерживаемых у источника выплаты, при применении "
    "специального налогового режима розничного налога в Тестовом районе "
    "с 4 (четырех) процентов на 3 (три) процента за налоговый период в 2026 году."
)

BOTH_REGIMES_NAMED = (
    "Решение маслихата Тестового района от 20 ноября 2025 года № 2. "
    "Понизить размер ставки корпоративного или индивидуального подоходного налога, "
    "за исключением налогов, удерживаемых у источника выплаты, при применении "
    "специального налогового режима розничного налога на основе упрощенной декларации "
    "в Тестовом районе с 4 (четырех) процентов на 3 (три) процента за налоговый период в "
    "2026 году."
)

# «частью первой … статьи 726», the phrasing ten published documents rely on
# (G25BE08332M and its siblings): the sentence names no regime by word at all,
# it cites the statutory base directly, and that must still confirm.
ARTICLE_726_PART_ONE = (
    "Решение маслихата Тестового района от 20 ноября 2025 года № 3. "
    "Понизить размер ставки, установленной частью первой статьи 726 Налогового кодекса "
    "Республики Казахстан, в Тестовом районе с 4 (четырех) процентов на 3 (три) процента "
    "за налоговый период в 2026 году."
)

# A benign Kazakh companion with no rate pair at all, so the Kazakh reader
# returns NO_MATCH rather than UNAVAILABLE — these tests are about the regime
# reader, not about the Kazakh transport path.
NO_KAZAKH_RATE = "Мәслихат шешімі. Ставка көрсетілмеген."


def test_a_retail_tax_sentence_refuses_rather_than_publishing_as_simplified() -> None:
    """The defect that was found live: seven districts, read correctly, wrong regime."""
    result = classify(RETAIL_ONLY, kazakh_text=NO_KAZAKH_RATE)
    assert result["outcome"] == CONFLICT
    assert "rate" not in result
    assert "regime objected" in result["reason"]
    assert "розничного налога" in result["reason"]


def test_a_sentence_naming_both_regimes_is_refused_as_ambiguous_not_guessed() -> None:
    """G25PD23040M's shape: one sentence, both regime names — no clause to prefer."""
    result = classify(BOTH_REGIMES_NAMED, kazakh_text=NO_KAZAKH_RATE)
    assert result["outcome"] == CONFLICT
    assert "rate" not in result
    assert "regime objected" in result["reason"]
    assert "BOTH" in result["reason"]


def test_a_non_genitive_retail_form_naming_both_regimes_is_ambiguous_not_confirmed() -> None:
    """The genitive-only bug: `розничному налогу` (dative) used to slip past retail entirely.

    `REGIME_RETAIL` used to match only the genitive `розничного налог\\w*`.
    `розничный налог`, `розничному налогу` and `розничном налоге` all missed
    it, and — because the "simplified alone" branch returned before the
    generic marker fallback was ever reached — a sentence naming BOTH the
    dative retail form and the simplified declaration confirmed silently as
    simplified. Widening the pattern to `розничн\\w*\\s+налог\\w*` and
    reordering the marker check ahead of the "simplified alone" return both
    close this; either alone was verified insufficient while diagnosing it.
    """
    dative_and_simplified = (
        "Решение маслихата Тестового района от 20 ноября 2025 года № 4. "
        "Понизить размер ставки при применении специального налогового режима "
        "розничному налогу на основе упрощенной декларации "
        "с 4 (четырех) процентов на 3 (три) процента за налоговый период в 2026 году."
    )
    reading = read_regime(dative_and_simplified)
    assert reading.kind == "substantive"
    assert "BOTH" in reading.detail

    result = classify(dative_and_simplified, kazakh_text=NO_KAZAKH_RATE)
    assert result["outcome"] == CONFLICT
    assert "rate" not in result


def test_article_726_part_one_still_confirms_with_no_regime_named() -> None:
    """Ten documents rely on this: no regime word at all, article cited directly."""
    result = classify(ARTICLE_726_PART_ONE, kazakh_text=NO_KAZAKH_RATE)
    regime = next(r for r in result["readings"] if r["reader"] == "regime")
    assert regime["rate_percent"] is None
    assert regime["kind"] == "no-match"
    assert result["outcome"] == CONFIRMED
    assert result["rate"] == 0.03


def test_the_regime_reader_directly() -> None:
    """`read_regime` in isolation, one branch at a time."""
    assert read_regime(RETAIL_ONLY).kind == "substantive"
    assert read_regime(BOTH_REGIMES_NAMED).kind == "substantive"
    assert read_regime(ARTICLE_726_PART_ONE).kind == "no-match"
    assert read_regime("с 4% на 3%, режим не указан.").kind == "no-match"


def test_the_regime_reader_refuses_a_corrupted_regime_name_rather_than_confirming() -> None:
    """G25UF33195M and G25UH00373M: `упрощ` + a corrupted character + `нной`.

    A real PDF-extraction artefact (U+04B0, a Kazakh letter, in place of the
    Cyrillic `е`), not a retail decision — but this reader cannot tell that
    from a genuine retail decision, and treats an unidentifiable regime the
    same way it treats a directly-named one: refuse, never guess.
    """
    corrupted = RETAIL_ONLY.replace("розничного налога", "на основе упрощҰнной декларации")
    reading = read_regime(corrupted)
    assert reading.kind == "substantive"
    assert "cannot identify" in reading.detail


def _corrupt(replacement: str) -> str:
    return REAL.replace(CLAUSE, replacement)


def test_a_wrong_word_alone_produces_a_conflict_not_a_row() -> None:
    """READER 2 wrong, 1 and 3 right. Exactly the case one pattern cannot see."""
    result = classify(_corrupt("с 4 (четырех) процентов на 3 (два) процента"))
    assert result["outcome"] == CONFLICT
    assert "rate" not in result


def test_a_wrong_digit_alone_produces_a_conflict_not_a_row() -> None:
    """READERS 1 and 3 wrong together, 2 right — they read the same numeral.

    That is the honest limit of this ensemble and it is why the word reader
    exists: without it, a corrupted digit would be unanimous.
    """
    result = classify(_corrupt("с 4 (четырех) процентов на 2 (три) процента"))
    assert result["outcome"] == CONFLICT
    assert "rate" not in result


def test_a_document_that_raises_the_rate_is_refused() -> None:
    result = classify(_corrupt("с 3 (три) процентов на 4 (четыре) процента"))
    assert result["outcome"] == CONFLICT
    assert "rate" not in result


def test_a_rate_outside_the_statutory_band_is_refused_even_if_every_reader_agrees() -> None:
    result = classify(_corrupt("с 4 (четырех) процентов на 9 (девять) процента"))
    assert result["outcome"] == CONFLICT
    assert "rate" not in result


def test_an_amended_or_repealed_document_is_refused_rather_than_read() -> None:
    """Its original text still extracts cleanly, and would be wrong."""
    result = classify("Утратило силу. " + REAL)
    assert result["outcome"] == UNPARSED
    assert "repealed" in result["reason"]


def test_a_document_with_no_rate_sentence_is_unparsed_not_guessed() -> None:
    result = classify("Решение маслихата о чем-то другом. Ставка не меняется.")
    assert result["outcome"] == UNPARSED
    assert "rate" not in result


def test_two_rate_sentences_are_refused_rather_than_chosen_between() -> None:
    sentence = classify(REAL, kazakh_text=REAL_KAZ)["sentence"]
    assert rate_sentence(f"{sentence} {sentence}") is None


def _rate_percent(result: dict[str, object]) -> int:
    """The percent the parser produced, never one this file supplied."""
    rate = result["rate"]
    assert isinstance(rate, float)
    return round(rate * 100)


def test_the_до_form_is_confirmed_and_its_number_is_written_in_its_own_sentence() -> None:
    """The queued conflict, resolved by teaching readers 1 and 3 the preposition.

    No expected rate appears here. The row is right if the three readers agree
    and the number they produced is the one printed in the sentence the parser
    kept — which is checkable against the document and not against a memory.
    """
    result = classify(DO_FORM, kazakh_text=DO_FORM_KAZ)
    assert result["outcome"] == CONFIRMED
    rate_readings = {r["rate_percent"] for r in result["readings"] if r["rate_percent"] is not None}
    assert rate_readings == {_rate_percent(result)}
    assert f"до {_rate_percent(result)} (" in result["sentence"]
    assert DO_CLAUSE in result["sentence"]


def test_each_reader_still_fails_alone_on_the_до_form() -> None:
    """The property the preposition fix could have destroyed.

    If teaching readers 1 and 3 the word `до` had made them accept whatever
    reader 2 accepts, no single-reader corruption would produce a conflict any
    more and the ensemble would be one reader in three coats.
    """
    corruptions = [
        # Reader 2 alone is wrong: the spelled word disagrees with the digit.
        "с 4 (четырех) процентов до 3 (двух) процентов",
        # Readers 1 and 3 alone are wrong: they share the numeral, reader 2 does not.
        "с 4 (четырех) процентов до 2 (трех) процентов",
    ]
    for corruption in corruptions:
        result = classify(DO_FORM.replace(DO_CLAUSE, corruption))
        assert result["outcome"] == CONFLICT, corruption
        assert "rate" not in result


def test_a_rate_rise_written_with_до_is_refused() -> None:
    """Evidence 005 in the phrasing that was not covered when it was written."""
    result = classify(DO_FORM.replace(DO_CLAUSE, "с 3 (трех) процентов до 4 (четырех) процентов"))
    assert result["outcome"] != CONFIRMED
    assert "rate" not in result


def test_до_does_not_match_inside_a_word() -> None:
    """`доходам` and `подоходного` both open with the preposition's letters.

    A reader that matched them would anchor on a number that is not a rate.
    """
    assert read_digit("по доходам 5 (пяти) процентов").rate_percent is None
    assert read_digit("подоходного 5 (пяти) процентов").rate_percent is None


def test_the_year_is_read_from_the_phrasings_the_documents_actually_use() -> None:
    """Each of the 19 discovered documents states the year in one of these.

    Reading only the first left `year` None on 15 of 16 confirmed rows, which
    switched off the transition reader's origin check without reporting it.
    """
    assert read_year("по доходам за налоговый период в 2026 году.") == 2026
    assert read_year("упрощенной декларации на 2026 год по Житикаринскому району.") == 2026
    assert read_year("Понизить в 2026 году размер ставки.") == 2026


def test_a_document_naming_two_years_yields_no_year_rather_than_a_guess() -> None:
    assert read_year("Понизить в 2026 году. Действовало в 2025 году.") is None


def test_a_meeting_date_is_not_read_as_the_tax_year() -> None:
    """`от 28 ноября 2025 года` is when the maslikhat sat, not what it taxed."""
    assert read_year("Решение маслихата от 28 ноября 2025 года № 147/50.") is None
    assert read_year("вводится в действие с 1 января 2026 года.") is None


def test_the_origin_check_actually_fires_on_a_document_phrased_like_these() -> None:
    """The guard that was inert, made to go red on the fixture that exposed it.

    `read_year` resolving is what supplies the base rate. If it regresses to
    None this assertion goes green for the wrong reason, so the year is
    asserted first.
    """
    assert classify(DO_FORM, kazakh_text=DO_FORM_KAZ)["year"] == 2026
    result = classify(DO_FORM.replace(DO_CLAUSE, "с 5 (пяти) процентов до 3 (трех) процентов"))
    assert result["outcome"] != CONFIRMED
    assert "rate" not in result


def test_no_outcome_other_than_confirmed_ever_carries_a_rate() -> None:
    """The single property that matters: a refusal must never look like a row."""
    corruptions = [
        "с 4 (четырех) процентов на 3 (два) процента",
        "с 4 (четырех) процентов на 2 (три) процента",
        "с 3 (три) процентов на 4 (четыре) процента",
        "с 4 (четырех) процентов на 9 (девять) процента",
    ]
    for corruption in corruptions:
        result = classify(_corrupt(corruption))
        assert result["outcome"] != CONFIRMED
        assert "rate" not in result


def test_the_transition_reader_alone_refusing_produces_a_conflict_not_a_row() -> None:
    """READER 3 alone refuses; readers 1, 2 and 4 all read 3 and are all correct.

    The starting rate is wrong — a понижение begins at the statutory base for
    the year — and reader 3 is the only one that can see it. Its refusal being
    treated as silence is what once confirmed a rate *rise* at 4%: two readers
    correctly read the new numeral, this one refused, and a refusal that counted
    as silence let the other two carry the result. Later its origin check sat
    inert on 15 of 16 rows, because the year it needs was read from one phrasing
    only and came back None, so the check skipped itself in silence.
    **This is the reader with the worst history and the least cover.**
    """
    result = classify(_corrupt("с 5 (пяти) процентов на 3 (три) процента"), kazakh_text=REAL_KAZ)
    assert result["outcome"] == CONFLICT
    assert "rate" not in result
    readings = {r["reader"]: r["rate_percent"] for r in result["readings"]}
    assert readings == {"digit": 3, "word": 3, "transition": None, "kazakh": 3, "regime": None}


def test_the_rise_case_defends_the_transition_reader_and_says_so() -> None:
    """Renamed from a name that described the document instead of the guard.

    On a rise the digit and word readers both read 4 and AGREE — correctly, on
    the numeral in front of them. Only the transition reader objects. A name
    that says "a rise is refused" reads as covered and gets weakened during a
    tidy-up; this one says which reader it defends.
    """
    result = classify(_corrupt("с 3 (три) процентов на 4 (четыре) процента"), kazakh_text=REAL_KAZ)
    readings = {r["reader"]: r["rate_percent"] for r in result["readings"]}
    assert readings == {"digit": 4, "word": 4, "transition": None, "kazakh": 3, "regime": None}
    assert result["outcome"] == CONFLICT
    assert "rate" not in result


def test_the_kazakh_reader_handles_all_three_published_shapes() -> None:
    """Measured across the 2026 set, not assumed from the common one.

    `N (word) пайыздан M (word) пайыз` in 17 of 19, digits-only in one, and
    percent signs with no word at all in one.
    """
    assert read_kazakh(REAL_KAZ).rate_percent == 3
    assert read_kazakh(ONCE_ONLY_KAZ).rate_percent == 3
    assert read_kazakh(PERCENT_ONLY_KAZ).rate_percent == 3


def test_the_kazakh_reader_tolerates_whitespace_inside_the_parentheses() -> None:
    """`( үш)`, `(төрт )` and `( екі)` all occur — three of nineteen.

    A stricter pattern refuses them SILENTLY, which reads as an unusual
    document rather than as a wrong regex.
    """
    assert read_kazakh("4 (төрт ) пайыздан 3 ( үш) пайыз.").rate_percent == 3


def test_the_kazakh_reader_refuses_a_numeral_it_has_never_seen() -> None:
    """бес and алты have not appeared in any document. Extrapolating is guessing."""
    assert read_kazakh("6 (алты) пайыздан 5 (бес) пайыз.").rate_percent is None


def test_the_kazakh_reader_refuses_when_its_own_digit_and_word_disagree() -> None:
    """The Kazakh file carries the same free check the Russian pair does."""
    assert read_kazakh("4 (төрт) пайыздан 3 (екі) пайыз.").rate_percent is None


def test_a_kazakh_that_contradicts_the_russian_produces_a_conflict_not_a_row() -> None:
    """The whole point of reader 4: two files cannot be corrupted the same way."""
    result = classify(REAL, kazakh_text=REAL_KAZ.replace("3 (үш) пайыз", "2 (екі) пайыз"))
    assert result["outcome"] == CONFLICT
    assert "rate" not in result


def test_a_kazakh_fetch_failure_is_reported_as_transport_not_as_disagreement() -> None:
    """Measured: G25SI00331M lost its Kazakh fetch to one transient TLS drop.

    It was reported exactly like a document whose Kazakh copy contradicts its
    Russian one, and it re-fetched cleanly moments later reading 3. Both block
    confirmation, which is right — but sending somebody to hunt a phrasing bug
    that does not exist is not.
    """
    result = classify(REAL, kazakh_error="SSLError: bad record mac")
    assert result["outcome"] == CONFLICT
    assert "transport failure" in result["reason"]
    assert "NOT a disagreement" in result["reason"]


def test_a_stable_kazakh_404_is_told_apart_from_a_transport_failure() -> None:
    """G25LC00433M and three siblings: a 404 that survived a retried fetch.

    Their Russian pages carry no `/kaz/docs/…` link at all — the document has
    no Kazakh copy published, which "re-run before reading this as a defect"
    actively misleads about. An HTTP status is a real answer, same rule
    `fetch()` states for the download step, so this is told apart from a
    genuine dropped connection rather than sharing its wording.
    """
    result = classify(REAL, kazakh_error="HTTPError: HTTP Error 404: ")
    assert result["outcome"] == CONFLICT
    assert "404" in result["reason"]
    assert "not a transport failure by itself" in result["reason"]
    assert "kaz/docs" in result["reason"]
    assert "NOT a disagreement" not in result["reason"]


def test_a_document_the_russian_readers_cannot_read_still_reports_what_kazakh_says() -> None:
    """Reporting a reading is not confirming it. One reading is never a row."""
    result = classify("Решение маслихата. Ставка не указана.", kazakh_text=ONCE_ONLY_KAZ)
    assert result["outcome"] != CONFIRMED
    assert "rate" not in result
    assert [r["rate_percent"] for r in result["readings"]] == [3]


BARE_WORDS = _fixture("G25NN00309M.txt")
BARE_PERCENT = _fixture("G25NJ00343M.txt")


def test_the_bare_word_form_confirms_from_two_sources() -> None:
    """`с 4 процентов до 3 процентов` — no parentheses, so reader 2 has nothing.

    It confirms because two DIFFERENT sources agree: the Russian numeral and
    the separately published Kazakh file. Reader 2's silence is a NO_MATCH — it
    says nothing about the rate — and does not block.
    """
    result = classify(BARE_WORDS, kazakh_text=ONCE_ONLY_KAZ)
    assert result["outcome"] == CONFIRMED
    assert result["rate"] == 0.03
    readings = {r["reader"]: r["rate_percent"] for r in result["readings"]}
    assert readings == {"digit": 3, "word": None, "transition": 3, "kazakh": 3, "regime": None}


def test_the_bare_percent_form_confirms_from_two_sources() -> None:
    """`с 4% до 3%` — no spelled word anywhere, and «процент» never appears."""
    result = classify(BARE_PERCENT, kazakh_text=PERCENT_ONLY_KAZ)
    assert result["outcome"] == CONFIRMED
    assert result["rate"] == 0.03
    assert "с 4% до 3%" in result["sentence"]


# A Kazakh document that is fetched and readable but states no rate pair: the
# Kazakh reader returns NO_MATCH, which is neither a transport failure nor an
# objection. It is the only way to reach the single-source rule, because every
# other refusal exits earlier — the first version of the two tests below used
# a fetch error and an empty Russian text, and BOTH stayed green when the rule
# was mutated away. They were testing branches above the one they named.
KAZ_WITHOUT_A_RATE = "Қостанай ауданының мәслихаты ШЕШТІ: осы шешім қолданысқа енгізілсін."


def test_the_two_russian_numeral_readers_alone_do_not_confirm() -> None:
    """The clause that matters: same substring, two coats, ONE reading.

    Readers 1 and 3 both take the numeral after `до` in the same clause, so
    their agreement is not corroboration — it is the same fact counted twice.
    Without a second SOURCE the document must not become a row, however
    confident the two look.
    """
    result = classify(BARE_WORDS, kazakh_text=KAZ_WITHOUT_A_RATE)
    readings = {r["reader"]: r["rate_percent"] for r in result["readings"]}
    assert readings == {"digit": 3, "word": None, "transition": 3, "kazakh": None, "regime": None}
    assert result["outcome"] == UNPARSED
    assert "only one independent reading" in result["reason"]
    assert "rate" not in result


def test_a_single_source_never_confirms_even_with_nothing_disagreeing() -> None:
    """Nothing objects, nothing disagrees, and it is still not a row."""
    result = classify(BARE_WORDS, kazakh_text=KAZ_WITHOUT_A_RATE)
    assert result["outcome"] != CONFIRMED
    assert "russian-numeral" in result["reason"]


def test_a_substantive_objection_still_blocks_a_two_source_agreement() -> None:
    """Widening the readers must not weaken the origin check.

    Readers 1, 2 and 4 all read 3 and are all correct; reader 3 objects because
    a понижение does not start from 5%. Two sources agree and it is still not a
    row — an objection is not outvoted.
    """
    result = classify(_corrupt("с 5 (пяти) процентов на 3 (три) процента"), kazakh_text=REAL_KAZ)
    assert result["outcome"] == CONFLICT
    assert "objected" in result["reason"]


# A real phrasing from the widened corpus: it states the rate and never states
# a tax year. 120 documents of this shape confirmed on 2026-08-13 with the
# origin check inert, which is evidence 006 recurring one corpus later.
# A Kazakh companion stating the SAME movement, so these cases turn on the
# year and not on a disagreement. Pairing the year-less Russian with the real
# Уральск Kazakh (which says 3) made two of these tests pass for the wrong
# reason — the third time today a test asserted the right outcome via the
# wrong branch.
YEARLESS_KAZ = "Атбасар ауданында 4 (төрт) пайыздан 2 (екі) пайызға дейін төмендетілсін."

YEARLESS = (
    "Решение маслихата Атбасарского района Акмолинской области от 20 ноября 2025 года № 100. "
    "Понизить размер ставки, установленной частью первой статьи 726 Налогового кодекса "
    "Республики Казахстан, в Атбасарском районе с 4 процентов на 2 процентов."
)


def test_a_rate_with_no_readable_year_is_not_confirmed() -> None:
    """**A check with no input has not passed — it could not run.**

    The origin test needs the statutory base for the document's year. Without a
    year the transition reader cannot discharge its obligation, so it refuses
    SUBSTANTIVE and the document cannot become a row however clearly the rate
    is written.
    """
    result = classify(YEARLESS, kazakh_text=YEARLESS_KAZ)
    assert result["outcome"] != CONFIRMED
    assert "rate" not in result
    # Every other reader agrees on 2 and is right; only the year is missing.
    assert {r["reader"]: r["rate_percent"] for r in result["readings"]}["kazakh"] == 2
    transition = next(r for r in result["readings"] if r["reader"] == "transition")
    assert transition["rate_percent"] is None
    assert "origin check cannot run" in transition["detail"]


def test_the_refusal_does_not_depend_on_knowing_the_phrasing() -> None:
    """The point of the fix: it holds for a phrasing nobody has seen.

    The previous fix enumerated the year phrasings then known, and the corpus
    later widened by an order of magnitude. This one keys on the absence of an
    input, not on a list of patterns.
    """
    invented = YEARLESS.replace("Атбасарском", "Небывалом")
    result = classify(invented, kazakh_text=YEARLESS_KAZ)
    transition = next(r for r in result["readings"] if r["reader"] == "transition")
    assert "origin check cannot run" in transition["detail"]
    assert result["outcome"] != CONFIRMED


def test_the_in_force_date_is_a_named_reader_and_is_labelled_as_one() -> None:
    """It reads a different sentence making a different claim, so it says so."""
    with_date = YEARLESS + " Настоящее решение вводится в действие с 01.01.2026."
    result = classify(with_date, kazakh_text=YEARLESS_KAZ)
    assert result["year"] == 2026
    assert result["year_source"] == "in-force-date"
    assert result["outcome"] == CONFIRMED


def test_a_year_stated_outright_is_labelled_differently_from_one_inferred() -> None:
    assert classify(REAL, kazakh_text=REAL_KAZ)["year_source"] == "tax-period"


def test_the_in_force_reader_reads_both_written_forms_and_only_january() -> None:
    assert read_year_from_in_force("вводится в действие с 01.01.2026") == 2026
    assert read_year_from_in_force("вводится в действие с 1 января 2026 года") == 2026
    # Not a plain calendar-year enactment, so it declines rather than assuming.
    assert read_year_from_in_force("вводится в действие с 15.07.2026") is None


def test_the_in_force_reader_skips_the_adilet_footnote() -> None:
    """adilet stamps "Сноска. Вводится в действие с <date> в соответствии с
    пунктом N настоящего решения" on 123 of 471 cached documents — a system
    footnote, not the decision's own clause. On 121 of those its date agrees
    with the decision's real clause, so the two collapse to one value and
    nothing was visibly wrong. On two it does not: G25PF02058M's footnote says
    01.01.2025 while its point 2 says 2026, and the mismatch made `found` a
    set of size 2 — a document stating its year perfectly plainly refused for
    having "two years".
    """
    footnote_then_real = (
        "Сноска. Вводится в действие с 01.01.2025 в соответствии с пунктом 2 "
        "настоящего решения. Далее следует текст. Настоящее решение вводится "
        "в действие с 1 января 2026 года и подлежит официальному опубликованию."
    )
    assert read_year_from_in_force(footnote_then_real) == 2026
    # The footnote alone, unaccompanied by a real clause, still yields nothing
    # — it is excluded outright, not merely outvoted by a second match.
    footnote_only = (
        "Сноска. Вводится в действие с 01.01.2025 в соответствии с пунктом 2 настоящего решения."
    )
    assert read_year_from_in_force(footnote_only) is None


def test_the_in_force_reader_tolerates_the_documents_own_typo() -> None:
    """G25FK28198M writes "вводиться" (with an extra ь) — a document error,
    not a parser one, but the marker refusing a one-letter misspelling of
    itself turned a plainly-dated document into an unparsed one.
    """
    typo_text = "Настоящее решение вводиться в действие с 1 января 2026 года."
    assert read_year_from_in_force(typo_text) == 2026


def test_the_real_makanshy_decision_confirms_via_the_typo_tolerant_marker() -> None:
    result = classify(IN_FORCE_TYPO)
    assert result["year"] == 2026
    assert result["year_source"] == "in-force-date"


def test_the_retroactive_effect_reader_is_named_and_reads_only_january() -> None:
    assert (
        read_year_from_retroactive_effect(
            "распространяется на правоотношения, возникшие с 1 января 2026 года."
        )
        == 2026
    )
    assert (
        read_year_from_retroactive_effect(
            "распространяется на правоотношения, возникшие с 01.01.2026."
        )
        == 2026
    )
    # Not 1 January, so this is not a plain calendar-year period — declined
    # rather than assumed, the same rule `read_year_from_in_force` follows.
    assert (
        read_year_from_retroactive_effect(
            "распространяется на правоотношения, возникшие с 15 июля 2026 года."
        )
        is None
    )
    assert read_year_from_retroactive_effect("не содержит такой фразы вовсе.") is None


def test_the_real_baizak_decision_confirms_via_the_retroactive_effect_reader() -> None:
    """G25GB00533M: no year `read_year` or `read_year_from_in_force` can read.

    Its entry-into-force date depends on the publication date and states no
    fixed date at all, so the in-force reader has nothing to read either — the
    year is stated only via the separate retroactive-effect clause in the same
    sentence.
    """
    assert read_year(RETROACTIVE_ONLY) is None
    assert read_year_from_in_force(RETROACTIVE_ONLY) is None
    result = classify(RETROACTIVE_ONLY, kazakh_text=RETROACTIVE_ONLY_KAZ)
    assert result["year"] == 2026
    assert result["year_source"] == "retroactive-effect"
    assert result["outcome"] == CONFIRMED
    assert result["rate"] == 0.02


def test_a_year_with_no_known_statutory_base_is_refused() -> None:
    """A check that cannot run has not passed, so the row must not confirm.

    The year here has to be one the statute does not reach. It was 2027 until
    the code was read and article 726 turned out to name no year at all, which
    gave 2027 a base and made this test pass for the wrong reason. Any year
    added to the table later will do the same, so this deliberately picks one
    far outside it rather than the next one along.
    """
    future = YEARLESS + " Настоящее решение вводится в действие с 01.01.2035."
    result = classify(future, kazakh_text=YEARLESS_KAZ)
    transition = next(r for r in result["readings"] if r["reader"] == "transition")
    assert "no statutory base known for year 2035" in transition["detail"]
    assert result["outcome"] != CONFIRMED


def test_the_limiter_is_one_shared_place_rather_than_one_per_script() -> None:
    """Every script fetches through here, so the promise holds without repetition.

    The specification and every planning document stated one request per second.
    The enumerator honoured it and extraction did not — nobody decided that, it
    was simply never written, so only the stated version was ever reviewed.

    The rate itself is the repository owner's to set and was raised to 3/second on
    2026-08-13, so this asserts the STRUCTURE and not the number: one place
    declares it, the interval is derived from it, and the enumerator's pause is
    that same value rather than a second constant typed to agree. Two constants
    written to agree drift in silence — that is how a stopword list stopped
    matching and 94 correctly-mapped rows collapsed to 1 with nothing going red.
    """
    import extract_rates

    assert extract_rates.MIN_REQUEST_INTERVAL == 1.0 / extract_rates.REQUESTS_PER_SECOND
    assert extract_rates.MIN_REQUEST_INTERVAL > 0
    assert enumerate_decisions.PAUSE_SECONDS == extract_rates.MIN_REQUEST_INTERVAL
    for module in (discover_decisions, enumerate_decisions):
        assert module.fetch is extract_rates.fetch

    # validate_readings.py fetches too (`emit_tasks` -> `cached_document`), but
    # through extract_rates's own `cached_document` rather than reimplementing
    # a fetch path of its own -- this was the enrolment missing here: nothing
    # asserted that the LLM validation gate's emitter goes through the same
    # limiter as everything else that talks to adilet.zan.kz.
    assert validate_readings.cached_document is extract_rates.cached_document


def test_the_limiter_actually_waits(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A limiter nobody has watched sleep is a constant, not a limiter."""
    import extract_rates

    monkeypatch.setattr(extract_rates, "MIN_REQUEST_INTERVAL", 0.2)
    monkeypatch.setattr(extract_rates, "_last_request_at", 0.0)
    started = time.monotonic()
    extract_rates._throttle()
    extract_rates._throttle()
    assert time.monotonic() - started >= 0.2


def test_a_cached_document_costs_no_request(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The delay bounds the rate; the cache bounds the total, and the total grows."""
    import extract_rates

    monkeypatch.setattr(extract_rates, "CACHE", tmp_path)
    payload = b"%PDF-1.4 fixture"
    (tmp_path / "TEST123.rus.pdf").write_bytes(payload)
    (tmp_path / "TEST123.rus.json").write_text(
        json.dumps(
            {"url": "https://example/TEST123.pdf", "sha256": hashlib.sha256(payload).hexdigest()}
        ),
        encoding="utf-8",
    )

    def refuse(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("a cache hit must not touch the network")

    monkeypatch.setattr(extract_rates, "fetch", refuse)
    monkeypatch.setattr(extract_rates, "pdf_url", refuse)
    url, cached = extract_rates.cached_document("TEST123")
    assert cached == payload
    assert url == "https://example/TEST123.pdf"


def test_a_cached_file_that_does_not_match_its_hash_is_refetched(  # type: ignore[no-untyped-def]
    tmp_path: Path, monkeypatch
) -> None:
    """A cached file that fails its own checksum is not a cached file."""
    import extract_rates

    monkeypatch.setattr(extract_rates, "CACHE", tmp_path)
    (tmp_path / "TEST123.rus.pdf").write_bytes(b"corrupted")
    (tmp_path / "TEST123.rus.json").write_text(
        json.dumps({"url": "https://example/x.pdf", "sha256": "0" * 64}), encoding="utf-8"
    )
    monkeypatch.setattr(extract_rates, "pdf_url", lambda *a, **k: "https://example/fresh.pdf")
    monkeypatch.setattr(extract_rates, "fetch", lambda *a, **k: b"%PDF fresh")
    url, payload = extract_rates.cached_document("TEST123")
    assert payload == b"%PDF fresh"
    assert (tmp_path / "TEST123.rus.pdf").read_bytes() == b"%PDF fresh"


def test_every_request_on_the_document_path_is_throttled(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The limiter must reach the caller, not merely exist.

    `fetch()` was proven to sleep in isolation while `pdf_url()` resolved its
    redirect through its own opener and never called the limiter — so each
    document made two requests and only one of them waited. **A check that
    exists and does not reach the thing it checks** is the shape that has cost
    this project the most, and extraction has already sat outside a rate limit
    that three documents stated.

    So this follows the real path — a cache miss, resolve, then download — and
    counts the limiter, rather than trusting that a throttled `fetch` implies a
    throttled document.
    """
    import extract_rates

    monkeypatch.setattr(extract_rates, "CACHE", tmp_path)
    calls: list[float] = []
    real_throttle = extract_rates._throttle

    def counting_throttle() -> None:
        calls.append(time.monotonic())
        real_throttle()

    monkeypatch.setattr(extract_rates, "_throttle", counting_throttle)
    monkeypatch.setattr(extract_rates, "MIN_REQUEST_INTERVAL", 0.05)
    monkeypatch.setattr(extract_rates, "_last_request_at", 0.0)

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://adilet.zan.kz/files/pdf/1/x.rus.pdf;jsessionid=ABC"

        def read(self) -> bytes:
            return b"%PDF-1.4 fixture"

    monkeypatch.setattr(
        extract_rates.urllib.request,
        "build_opener",
        lambda *a, **k: type("O", (), {"open": lambda self, *a, **k: _Response()})(),
    )
    monkeypatch.setattr(extract_rates.urllib.request, "urlopen", lambda *a, **k: _Response())

    url, payload = extract_rates.cached_document("TESTDOC")
    assert payload == b"%PDF-1.4 fixture"
    assert ";jsessionid=" not in url
    # Two requests happen on a cache miss: resolve the redirect, then download.
    assert len(calls) == 2, f"one of the document's requests skipped the limiter: {calls}"


def test_pdf_url_retries_a_transient_drop_but_not_an_http_status(  # type: ignore[no-untyped-def]
    monkeypatch,
) -> None:
    """`fetch()` retries a dropped connection; `pdf_url()` did not, on a request
    to the same flaky host. Four Kazakh fetches (G25LC00433M, G25LF00353M,
    G25LI00366M, G26BM08572M) reached the queue as UNAVAILABLE from exactly
    one failed attempt — and re-probing G25LC00433M's own URL during this
    session returned a *different* transient error on a later try, which is
    evidence this step is as unreliable as the download step, not that these
    four documents lack a Kazakh copy.

    A genuine HTTP status is still never retried — a 404 is a real answer,
    same rule `fetch()` states for the download step.
    """
    import extract_rates

    monkeypatch.setattr(extract_rates.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(extract_rates, "_last_request_at", 0.0)

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def geturl(self) -> str:
            return "https://adilet.zan.kz/files/pdf/1/x.kaz.pdf"

    class _FlakyThenOK:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, *args: object, **kwargs: object) -> _Response:
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("simulated transient drop")
            return _Response()

    opener = _FlakyThenOK()
    monkeypatch.setattr(extract_rates.urllib.request, "build_opener", lambda *a, **k: opener)

    url = extract_rates.pdf_url("TESTDOC", language="kaz")
    assert url == "https://adilet.zan.kz/files/pdf/1/x.kaz.pdf"
    assert opener.calls == 2, "a transient drop must be retried, not treated as final"

    class _AlwaysNotFound:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, *args: object, **kwargs: object) -> _Response:
            self.calls += 1
            raise urllib.error.HTTPError(
                "https://x", 404, "Not Found", email.message.Message(), None
            )

    not_found = _AlwaysNotFound()
    monkeypatch.setattr(extract_rates.urllib.request, "build_opener", lambda *a, **k: not_found)
    with pytest.raises(urllib.error.HTTPError) as raised:
        extract_rates.pdf_url("TESTDOC", language="kaz")
    assert raised.value.code == 404
    assert not_found.calls == 1, "a real HTTP status must not be retried"
