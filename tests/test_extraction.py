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
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import discover_decisions
import enumerate_decisions
from extract_rates import (
    CONFIRMED,
    CONFLICT,
    UNPARSED,
    classify,
    rate_sentence,
    read_digit,
    read_kazakh,
    read_transition,
    read_word,
    read_year,
    read_year_from_in_force,
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
    """Three over the Russian text, one over the separately published Kazakh file."""
    readings = classify(REAL, kazakh_text=REAL_KAZ)["readings"]
    assert [reading["rate_percent"] for reading in readings] == [3, 3, 3, 3]
    assert [reading["reader"] for reading in readings] == ["digit", "word", "transition", "kazakh"]


def test_the_digit_reader_reads_digits_only() -> None:
    assert read_digit(CLAUSE).rate_percent == 3
    assert read_digit("с четырех процентов на три процента").rate_percent is None


def test_the_word_reader_reads_words_only_and_does_not_consult_the_digit() -> None:
    """Proven by disagreeing with the digit when the two are made to differ."""
    assert read_word(CLAUSE).rate_percent == 3
    assert read_word("с 4 (четырех) процентов на 3 (два) процента").rate_percent == 2
    assert read_word("с 4 процентов на 3 процента").rate_percent is None


def test_the_transition_reader_refuses_a_rise_and_a_wrong_starting_point() -> None:
    assert read_transition(CLAUSE, 2026).rate_percent == 3
    assert read_transition("с 3 (три) процентов на 4 (четыре) процента", 2026).rate_percent is None
    assert read_transition("с 5 (пяти) процентов на 3 (три) процента", 2026).rate_percent is None


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
    assert {reading["rate_percent"] for reading in result["readings"]} == {_rate_percent(result)}
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
    assert readings == {"digit": 3, "word": 3, "transition": None, "kazakh": 3}


def test_the_rise_case_defends_the_transition_reader_and_says_so() -> None:
    """Renamed from a name that described the document instead of the guard.

    On a rise the digit and word readers both read 4 and AGREE — correctly, on
    the numeral in front of them. Only the transition reader objects. A name
    that says "a rise is refused" reads as covered and gets weakened during a
    tidy-up; this one says which reader it defends.
    """
    result = classify(_corrupt("с 3 (три) процентов на 4 (четыре) процента"), kazakh_text=REAL_KAZ)
    readings = {r["reader"]: r["rate_percent"] for r in result["readings"]}
    assert readings == {"digit": 4, "word": 4, "transition": None, "kazakh": 3}
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
    assert readings == {"digit": 3, "word": None, "transition": 3, "kazakh": 3}


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
    assert readings == {"digit": 3, "word": None, "transition": 3, "kazakh": None}
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
