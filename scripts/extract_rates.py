"""adilet.zan.kz decision PDF -> a rate, or a refusal that says why.

Replaces the hand-entry design in `docs/SPEC.md` §6.2, whose premise — that
`adilet.zan.kz` refuses automated fetches — was probed and did not hold: the site
serves, its full-text search returns document ids, each decision is a PDF whose
text extracts cleanly, and the rate is stated in that text. The only barrier was
an incomplete TLS certificate chain, handled below.

The rule it does not replace: **no number enters this dataset unless it was read
out of the decision.** A deterministic reader is a better reader than a person
at row 150; a language model is not a reader at all and none is used here.

## Why several readers rather than one pattern

One pattern that matches the wrong number returns a confident, well-formed,
wrong rate, and nothing downstream can tell. So the document is read five ways
— four that can produce a rate (digit, word, transition, kazakh) and a fifth,
`read_regime`, that never produces one and only objects — and **disagreement
is the output**, not an average:

  - `confirmed` — **at least two of the four rate-bearing readers agree, from
    two different sources**, no reader raises a substantive objection, and the
    sanity rules hold. Only these become rows. A reader that refuses blocks
    confirmation: its refusal is evidence, not silence. `read_regime` never
    supplies the agreement itself — it can only veto one.
  - `conflict`  — readers disagree, or one objects while the rest read cleanly
    (a rate rise, a wrong starting point, a document naming the wrong regime).
    The dangerous case, and the reason this module exists in this shape.
  - `unparsed`  — nothing matched, or only one independent source did. Safe,
    but it is silent coverage loss, so it is counted rather than shrugged at.

**The readers must stay independent, and independence decays quietly.** If two
of them ever start deriving from the same substring the same way, they agree
with each other while both are wrong, and every test still passes. Each reader
below states what it reads and how it differs from the others. Tests feed text
where exactly one reader is wrong and assert a `conflict` rather than a row.

## What it never does

It never chooses between disagreeing readers, never fills a gap with the base
rate or with what most decisions say, and never writes `data/rates.csv` — that
file is a human's, and a test asserts no script opens it for writing.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, NamedTuple

from validate import RATE_MAX, RATE_MIN, REPO_ROOT

BASE_URL = "https://adilet.zan.kz"

# The site sends only its leaf certificate, so a default trust store cannot
# build a chain and the fetch fails with "unable to get local issuer
# certificate". That is a missing intermediate, NOT a refusal to serve us, and
# SPEC.md §6.2's ROBOTS_DISALLOWED claim does not hold.
#
# The intermediate is committed beside this file rather than downloaded, and
# **verification stays on**. Disabling it (`curl -k`, `verify=False`) would turn
# a fixable trust problem into a permanent inability to detect a hostile
# response — on the one connection whose authenticity the whole dataset rests.
#
# GoGetSSL G2 TLS RSA4096 SHA256 2022 CA-1, issued by DigiCert Global Root G2,
# valid to 2032-06-22, sha256 fingerprint
# 8A:AD:F0:68:A1:B7:C0:4B:3E:34:6F:7C:97:FD:96:19:FF:F1:4E:CC:6C:82:C2:F1:55:94:B9:73:2F:3F:3E:72
CHAIN = Path(__file__).resolve().parent / "adilet-chain.pem"

# **One request per second, everywhere, enforced here rather than per script.**
#
# The specification and every planning document stated this rate. The enumerator
# honoured it; extraction never did, and nobody decided that — it was simply
# never written, so only the stated version was ever reviewed. A rate limit we
# describe and do not implement makes the promise false, and this promise was
# made to nobody but ourselves, which is the easiest kind to break.
#
# `robots.txt` on this host disallows automated access. We proceed because the
# material is public legal text and because we said we would behave. "It has
# not been rejected yet" is not evidence the rate is acceptable — it is
# evidence we have not found the limit.
# Raised from 1 request/second to 3 by the repository owner, 2026-08-13, as a
# deliberate call about how hard to lean on someone else's server.
#
# THIS IS THE ONLY PLACE THE RATE IS SET. `enumerate_decisions.py` imports it
# rather than declaring its own — two constants typed to agree drift silently,
# which is how a stopword list stopped matching and 94 correctly-mapped rows
# collapsed to 1 with nothing going red.
REQUESTS_PER_SECOND = 3.0
MIN_REQUEST_INTERVAL = 1.0 / REQUESTS_PER_SECOND
_last_request_at = 0.0

# The delay bounds the RATE; the cache bounds the TOTAL, and the total is what
# grows — 19 documents, then 177, and coverage pushes it higher. A cached
# document costs no request at all, so a re-parse is free. Same precedent as
# `fetch_kato.py --workbook`.
CACHE = REPO_ROOT / ".cache" / "documents"

EXTRACTED = REPO_ROOT / "data" / "extracted-rates.json"
QUEUE = REPO_ROOT / "data" / "extraction-queue.json"

# Article 726 of the Tax Code of the Republic of Kazakhstan, 18 July 2025
# № 214-VIII ЗРК, read from the act itself:
#
#   «…производится налогоплательщиком самостоятельно путем применения к объекту
#    налогообложения за отчетный налоговый период ставки в размере 4 процентов.»
#
# The article names no year, so the base holds for as long as the code does. A
# "понижение" must start from it, and a document claiming to lower the rate from
# anything else is not the document we think it is.
#
# 2027 is listed because a decision adopted in 2026 for 2027 already exists and
# was refused for want of a base. The same article governs it.
BASE_RATE_PERCENT = {2026: 4, 2027: 4}

# **The same article permits councils to RAISE the rate, not only lower it:**
# «Местные представительные органы имеют право понижать или повышать размер
# ставки … не более чем на 50 процентов». So the lawful range is 2% to 6%, and a
# decision that raises a rate is a valid decision, not a defect.
#
# This pipeline cannot see one. Discovery searches for «понижени», and the
# transition reader treats new >= old as a refusal. No document in the corpus so
# far actually raises, so nothing is currently wrong — but a district that did
# raise its rate would be absent rather than reported, and absence here is
# already the failure the project works hardest to avoid.
#
# Fixing it means widening discovery to «повышени» and teaching the transition
# reader that direction is a property to record rather than a test to pass.
# Deliberately not done in the same change that found it.
COUNCILS_MAY_ALSO_RAISE = True

# Article 726 again, and it is why the in-force date is a sound source for the
# tax year rather than a convention: «Такое решение … принимается … не позднее
# 1 декабря года, предшествующего году его введения, вводится в действие с
# 1 января года, следующего за годом его принятия».
YEAR_FOLLOWS_IN_FORCE_DATE_BY_STATUTE = True

# Читается независимо от цифры. Genitive and nominative both occur: the old
# rate is written "с 4 (четырех) процентов", the new one "на 3 (три) процента".
# **Never build this table from the digits.** A word reader that looks up the
# spelling of the digit it just read is one reader wearing two coats.
NUMERAL_WORDS = {
    "один": 1,
    "одного": 1,
    "два": 2,
    "двух": 2,
    "две": 2,
    "три": 3,
    "трех": 3,
    "трёх": 3,
    "четыре": 4,
    "четырех": 4,
    "четырёх": 4,
    "пять": 5,
    "пяти": 5,
    "шесть": 6,
    "шести": 6,
}

# The preposition that introduces the NEW rate. `на` is the common form; `до`
# says the same thing and occurs — `с 4 (четырех) процентов до 3 (трех)
# процентов`, Щербактинский район, G25PM14750M, which reached the queue as a
# conflict because readers 1 and 3 knew only `на` while reader 2 does not
# anchor on a preposition at all and read the rate fine.
#
# **Readers 1 and 3 share this vocabulary; reader 2 must never use it.** Sharing
# it does not merge them: they already anchor on the same word, and their
# independence is in what each does afterwards — reader 1 takes the
# destination, reader 3 takes the whole movement and refuses a direction reader
# 1 cannot see. Reader 2 stays independent precisely by ignoring the
# preposition and reading the last spelled numeral instead.
TO_NEW_RATE = r"(?:на|до)"

# How a decision names the rate it lowers FROM. `с 4 (четырех) процентов` is the
# common form; Astana makes the origin a property of the article it cites,
# «ставку, установленную частью первой … в размере 4 процентов, до 3 процентов»,
# with no `с` anywhere. The sentence selector found no rate clause at all, so the
# capital sat in the queue unparsed.
#
# Widening a reader is the move that empties a queue while breaking a dataset,
# so this stays narrow: `в размере` must be followed by a number and a percent
# word, exactly as `с` must.
FROM_OLD_RATE = r"(?:с|в\s+размере)"

# READER 4's vocabulary. Only the three numerals actually observed across the
# nineteen 2026 decisions. **бес (5) and алты (6) are deliberately absent**:
# adding them would be extrapolation, and an unknown word must refuse rather
# than be guessed at.
KAZAKH_NUMERALS = {"екі": 2, "үш": 3, "төрт": 4}

# The Kazakh rate pair, in all three shapes the documents actually use:
#
#   4 (төрт) пайыздан 3 (үш) пайыз     17 of 19
#   4 пайыздан 3 пайыз                 G25NN00309M — digits only
#   4%-дан 3%                          G25NJ00343M — percent signs only
#
# The optional inner whitespace is not defensive tidiness: `( үш)`, `(төрт )`
# and `( екі)` all occur, three of nineteen. Requiring `\(\w+\)` would refuse
# those documents **silently**, which is the worst failure shape here — a
# perfectly readable document reported as unreadable.
KAZAKH_PAIR = re.compile(
    r"(\d+)\s*(?:\(\s*([^()]{1,15}?)\s*\))?\s*(?:пайыз\S*|%\S*)"
    r"[^.\d]{0,24}?\s*(\d+)\s*(?:\(\s*([^()]{1,15}?)\s*\))?\s*(?:пайыз|%)"
)

# A document that has been amended or repealed still serves its original text,
# which would extract cleanly and be wrong. Refuse it and say so.
SUPERSEDED_MARKERS = (
    "Утратил силу",
    "Утратило силу",
    "Утратила силу",
    "внесены изменения",
    "Внесены изменения",
)

# Article 726 also establishes a SEPARATE special regime, розничный налог
# (retail tax), whose rate councils lower with a near-identical sentence —
# same verb ("Понизить"/"Установить снижение"), same "с X на|до Y" shape, same
# article. Nothing above tells the two apart: a retail-tax decision reads
# cleanly, every reader agrees, and the row confirms — wrong regime entirely.
# Measured 2026-08-14: seven published districts were exactly that — Жуалынский,
# Жетысайский, Казыгуртский, Келесский, Ордабасынский, Отрарский and Улытауский —
# read correctly and published as if they were simplified-declaration rates. An
# eighth, Актогайский, names both regimes in one clause and is refused as
# ambiguous rather than decided.
#
# So the regime is checked on THE SAME SENTENCE the rate comes from, never the
# document as a whole — a title can name one regime while the operative
# clause governs the other. Measured on G25GD00552M and two siblings: the
# title says «упрощенной декларации», the "Понизить…" clause that actually
# carries the rate says «розничного налога».
# Genitive-only (`розничного`) missed the nominative, dative and prepositional
# forms — `розничный налог`, `розничному налогу`, `розничном налоге` — which
# all occur in real decisions and were confirmed to fall through to `simplified`
# unchallenged. Widened to the stem plus any Cyrillic suffix rather than an
# enumerated list of inflections: a stopword list keyed to specific endings is
# exactly the shape that stopped matching silently once before (94 rows
# collapsed to 1 when a stem length moved by one character). Measured: widening
# changes the verdict on zero of the currently cached documents — the corpus
# so far only ever wrote the genitive — so this is a forward guard, not a
# correction to any confirmed row.
REGIME_RETAIL = re.compile(r"розничн\w*\s+налог\w*")
REGIME_SIMPLIFIED = re.compile(r"упрощ(?:е|ё)нн\w*\s+деклараци\w*")

# The generic phrase that introduces EITHER regime name. Present in the rate
# sentence but followed by neither spelling above is not silence — it is a
# document naming a regime this reader cannot read, and that is a real
# failure mode, not a hypothetical one: G25UF33195M and G25UH00373M extract
# «упрощ» + one corrupted character (U+04B0, a Kazakh letter, not a Cyrillic
# «е») + «нной», where «упрощенной» belongs — a PDF-extraction artefact, not a
# retail-tax decision, but this reader cannot tell that from a genuine retail
# decision and must not guess either way.
#
# Widening REGIME_SIMPLIFIED to tolerate the corruption would make it
# invisible and confirm the row anyway — the same shape of mistake as the
# stopword list that stopped matching when a stem length moved by one
# character. An unidentifiable regime refuses; it is never assumed to be the
# one this dataset publishes.
REGIME_MARKER = re.compile(r"специальн\w*\s+налогов\w*\s+режим\w*")

CONFIRMED, CONFLICT, UNPARSED = "confirmed", "conflict", "unparsed"

# Marks a reading that failed to happen rather than one that disagreed. The
# distinction is the difference between "re-run this" and "read this document".
UNAVAILABLE = "UNAVAILABLE: "


# Why a reader produced nothing. **The three are not interchangeable.**
#
#   READ         it produced a rate.
#   NO_MATCH     the phrasing is not one it knows. Says nothing about the rate,
#                so it does not block a confirmation another source supports.
#   SUBSTANTIVE  it read the document and objected — a rise, a wrong starting
#                point, a spelled numeral contradicting its own digit. **This
#                always blocks.** A refusal is evidence, not silence.
#   UNAVAILABLE  the file could not be fetched. Not a reading at all.
READ, NO_MATCH, SUBSTANTIVE, UNAVAILABLE_KIND = "read", "no-match", "substantive", "unavailable"

# Which file, and which substring of it, a reader takes its number from.
# **Independence is a property of the sources, not a count of functions.**
# Readers 1 and 3 both read the numeral after `на`/`до` in the same clause, so
# they share every failure of that clause and count as ONE reading. Reader 2
# reads the spelled word; reader 4 reads a different file altogether.
READER_SOURCE = {
    "digit": "russian-numeral",
    "transition": "russian-numeral",
    "word": "russian-word",
    "kazakh": "kazakh-file",
    # `read_regime` never returns a rate, so this entry is never actually
    # looked up by the `rate_percent is not None` filter below — but a
    # dict indexed by every reader's name and missing one is a KeyError
    # waiting for the day that filter's guard changes, not a fact worth
    # relying on staying true forever.
    "regime": "regime-check",
}


class Reading(NamedTuple):
    """One reader's answer, why it has none, and what it read to get there."""

    reader: str
    rate_percent: int | None
    detail: str
    kind: str = READ


def _throttle() -> None:
    """Sleep until at least MIN_REQUEST_INTERVAL has passed since the last request."""
    global _last_request_at  # noqa: PLW0603 — one process-wide limiter is the point
    wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def fetch(url: str, attempts: int = 3) -> bytes:
    """GET over a verified TLS connection, with the missing intermediate supplied.

    Retries a transport failure and nothing else. This host drops a connection
    mid-body every so often — `SSLError: DECRYPTION_FAILED_OR_BAD_RECORD_MAC`,
    seen on both a large download and a search page — and a single attempt turns
    that into a document we wrongly record as unreachable. An HTTP status is a
    real answer and is never retried.

    The pause between attempts is also the courtesy delay: this is one small
    government site and the whole dataset is a few hundred documents a year.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(CHAIN))
    request = urllib.request.Request(url, headers={"User-Agent": "kz-tax-rates/0 (+dataset build)"})

    for attempt in range(1, attempts + 1):
        try:
            _throttle()
            with urllib.request.urlopen(request, timeout=60, context=context) as response:  # noqa: S310
                payload: bytes = response.read()
            return payload
        except (ssl.SSLError, TimeoutError, http.client.IncompleteRead, urllib.error.URLError):
            if attempt == attempts:
                raise
            time.sleep(attempt * 2)
    raise AssertionError("unreachable")  # pragma: no cover


class _KeepRedirect(urllib.request.HTTPRedirectHandler):
    """Stop at the redirect instead of following it.

    The Location carries `;jsessionid=…` and that URL 404s, so following it
    automatically — which is what urlopen does by default — fails on a document
    that is served perfectly well. The session id has to come off first.
    """

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def pdf_url(document_id: str, language: str = "rus") -> str:
    """Resolve /<language>/docs/<id>/download to the clean file URL it points at.

    `kaz` is a separate published file, not a translation widget: reader 4 reads
    it, and that is where its independence comes from.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(CHAIN))
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), _KeepRedirect
    )
    request = urllib.request.Request(
        f"{BASE_URL}/{language}/docs/{document_id}/download",
        headers={"User-Agent": "kz-tax-rates/0 (+dataset build)"},
    )
    try:
        # The redirect resolution is a request like any other. It sat outside
        # the limiter until 2026-08-13 — so every document made two requests
        # and only one of them waited, which is exactly the shape the limiter
        # was written to end. Found by the test that follows the document path
        # rather than testing fetch() in isolation.
        _throttle()
        with opener.open(request, timeout=60) as response:
            resolved: str = response.geturl()
    except urllib.error.HTTPError as error:
        if error.code not in (301, 302, 303, 307, 308):
            raise
        resolved = error.headers["Location"]
    return re.sub(r";jsessionid=[^?&]*", "", resolved)


def cached_document(
    document_id: str, language: str = "rus", refresh: bool = False
) -> tuple[str, bytes]:
    """The document's PDF bytes, from the cache when we already have them.

    Keyed on document id and language, with the URL and sha256 stored beside
    the bytes so a cached copy can be checked rather than trusted. A cache hit
    costs no request, which is what keeps a growing corpus from turning into a
    growing load.

    The cache is not committed: it is reproducible from the site, and the
    provenance that matters — the sha256 — is recorded on every extracted row.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    payload_path = CACHE / f"{document_id}.{language}.pdf"
    meta_path = CACHE / f"{document_id}.{language}.json"

    if payload_path.exists() and meta_path.exists() and not refresh:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        payload = payload_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() == meta.get("sha256"):
            return str(meta["url"]), payload
        # A cached file that does not match its own hash is not a cached file.

    url = pdf_url(document_id, language)
    payload = fetch(url)
    payload_path.write_bytes(payload)
    meta_path.write_text(
        json.dumps(
            {"url": url, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return url, payload


def pdf_text(payload: bytes) -> str:
    """The document's text, whitespace normalised to single spaces.

    **The one seam with a third-party dependency.** Two standard-library
    extractors were written and measured against this project's own reference
    document, and both silently corrupted the exact sentence the readers parse:
    `4 (четырех) процентов` came out as `4 (четырех процентов`, because a PDF
    literal string may contain balanced parentheses and these documents encode
    text as two-byte CIDs in which 0x28/0x29 appear as ordinary data. A closing
    bracket lost there is invisible and lands inside the number.

    So the extraction backend is isolated here, behind one function with one
    return type, and swapping it changes nothing else.
    """
    import io

    from pypdf import PdfReader  # noqa: PLC0415

    reader = PdfReader(io.BytesIO(payload))
    return re.sub(r"\s+", " ", " ".join(page.extract_text() for page in reader.pages)).strip()


def rate_sentence(text: str) -> str | None:
    """The single sentence that lowers the rate, verbatim.

    More than one is a refusal, not a choice: a document with two rate
    sentences is a document this parser does not understand.
    """
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.]) ", text)
        if ("процент" in sentence or "%" in sentence)
        and re.search(rf"\b{FROM_OLD_RATE}\s*\d", sentence)
        and re.search(rf"\b{TO_NEW_RATE}\s+\d", sentence)
    ]
    return sentences[0] if len(sentences) == 1 else None


def read_digit(sentence: str) -> Reading:
    """READER 1 — the numeral in the `на …` clause.

    Reads Arabic digits only, anchored on the preposition that introduces the
    new rate. Blind to the spelled word.
    """
    match = re.search(rf"\b{TO_NEW_RATE}\s+(\d+)\s*(?:\(|%|процент)", sentence)
    if not match:
        return Reading("digit", None, "no `на|до <digit>` clause in the sentence", NO_MATCH)
    return Reading("digit", int(match.group(1)), match.group(0).strip())


def read_word(sentence: str) -> Reading:
    """READER 2 — the spelled numeral, through its own table.

    Independent of reader 1 in both its input and its rule: it reads Cyrillic
    words, never digits, and it takes the LAST `(<слово>) процент…` in the
    sentence rather than anchoring on `на`. In a понижение the new rate is the
    second of the two, so the two readers reach the same fact by different
    routes and can disagree.
    """
    found = re.findall(r"\(([А-Яа-яЁё]+)\)\s*процент", sentence)
    if not found:
        return Reading("word", None, "no `(<слово>) процент` in the sentence", NO_MATCH)
    word = found[-1].lower()
    if word not in NUMERAL_WORDS:
        return Reading("word", None, f"unknown numeral word {word!r}", SUBSTANTIVE)
    return Reading("word", NUMERAL_WORDS[word], f"({word}) процент")


def read_transition(sentence: str, year: int | None) -> Reading:
    """READER 3 — the `с X … на Y` pair, checked for direction and origin.

    Reads the whole movement rather than the destination, so it can refuse what
    the other two cannot see: a rate that did not go down, or one that started
    from something other than the statutory base for that year.
    """
    match = re.search(
        rf"\b{FROM_OLD_RATE}\s+(\d+)\s*(?:\(|%|процент).*?\b{TO_NEW_RATE}\s+(\d+)\s*(?:\(|%|процент)",
        sentence,
    )
    if not match:
        return Reading("transition", None, "no `с|в размере X … на|до Y` pair", NO_MATCH)
    old, new = int(match.group(1)), int(match.group(2))
    if new >= old:
        return Reading("transition", None, f"not a понижение: {old} -> {new}", SUBSTANTIVE)
    # **A check with no input has not passed — it could not run.** The origin
    # test needs the statutory base for the document's year, so without a year
    # this reader cannot discharge its own obligation and must REFUSE.
    #
    # It returned the rate anyway until 2026-08-13, and 120 documents confirmed
    # with the origin check inert — evidence 006 recurring, because that fix
    # enumerated the phrasings then known and the corpus later widened by an
    # order of magnitude. **A guard that depends on the completeness of a list
    # reopens every time the list is outgrown.** This one does not.
    base = BASE_RATE_PERCENT.get(year or 0)
    if base is None:
        return Reading(
            "transition",
            None,
            f"no statutory base known for year {year!r} — the origin check cannot run",
            SUBSTANTIVE,
        )
    if old != base:
        return Reading(
            "transition", None, f"starts from {old}%, not the {base}% base for {year}", SUBSTANTIVE
        )
    return Reading("transition", new, match.group(0).strip())


def read_regime(sentence: str) -> Reading:
    """READER 5 — which special regime the rate sentence actually governs.

    Never originates a rate; it only objects. A sentence naming «розничного
    налога» (retail tax) and not «упрощенной декларации» is not a
    simplified-declaration rate at all, however cleanly the other four
    readers parse its digits — the two regimes cite the same article and
    move by the same shape of sentence, so the digit/word/transition/kazakh
    readers cannot see the difference and were not designed to.

    - both names in one sentence → refuse ambiguous: which clause is
      operative is not this reader's call (`G25PD23040M`: одно предложение,
      «розничного налога на основе упрощенной декларации», оба слова).
    - «розничного» alone → refuse, naming the regime.
    - «упрощенной декларации» alone → fine, this is the regime published here.
    - neither name, but the generic "специального налогового режима" marker
      is present → the regime cannot be identified (see REGIME_MARKER above)
      → refuse rather than assume it is the one this dataset publishes. This
      is checked BEFORE the "simplified alone" case below, not after: a
      pattern that misses a real retail phrasing (the genitive-only bug fixed
      alongside this one) must not let an unrelated `simplified` match in the
      same sentence short-circuit past the marker on its way out. Widening a
      name pattern can always miss a future inflection; a marker check that
      an early return can skip is a hole no widening closes for good.
    - neither name and no marker at all → says nothing about the regime one
      way or the other; left to the other readers. This is the COMMON case:
      documents that cite «частью первой … статьи 726» directly never name a
      regime by word (`G25BE08332M` and its siblings), and some cite article
      726 without even «частью первой» (`G25BI84692M`) — both are genuine
      simplified-declaration rows this reader must not touch.
    - «упрощенной декларации» alone, with no unexplained marker → fine, this
      is the regime published here.
    """
    retail = REGIME_RETAIL.search(sentence)
    simplified = REGIME_SIMPLIFIED.search(sentence)
    marker = REGIME_MARKER.search(sentence)
    if retail and simplified:
        return Reading(
            "regime",
            None,
            "sentence names BOTH the retail tax regime (розничного налога) and the "
            f"simplified declaration (упрощенной декларации) — cannot tell which governs: "
            f"{sentence!r}",
            SUBSTANTIVE,
        )
    if retail:
        return Reading(
            "regime",
            None,
            "sentence governs the retail tax regime (розничного налога) — a separate "
            f"special regime under the same article, not a simplified-declaration rate: "
            f"{sentence!r}",
            SUBSTANTIVE,
        )
    if marker and not simplified:
        return Reading(
            "regime",
            None,
            "sentence names a special regime this reader cannot identify — neither "
            f"'розничного налога' nor 'упрощенной декларации' matched: {sentence!r}",
            SUBSTANTIVE,
        )
    if simplified:
        return Reading("regime", None, "governs the simplified declaration regime", NO_MATCH)
    return Reading("regime", None, "no regime named in the sentence", NO_MATCH)


# The tax year the rate applies to, said three ways. **Reading only the first
# of them switched off a check without saying so:** 15 of the 16 documents
# confirmed on 2026-08-12 phrase the period differently, so `year` was None,
# so `read_transition`'s "starts from the statutory base" test found no base to
# compare against and skipped itself. Every one of those rows confirmed with
# the ensemble's only origin check inert. Measured, not suspected — the
# committed `data/extracted-rates.json` carries `"year": null` on all 15.
#
# None of these reads a date: `от 28 ноября 2025 года` is when the maslikhat
# sat, and the year the rate applies to is a different fact that the document
# states separately.
YEAR_PATTERNS = (
    r"за налоговый период в (\d{4}) году",  # "…по доходам … за налоговый период в 2026 году"
    r"\bна (\d{4}) год\b",  # "…упрощенной декларации на 2026 год по X району"
    r"\bв (\d{4}) году\b",  # "Понизить в 2026 году размер ставки…"
)


def read_kazakh(kazakh_text: str | None, unavailable: str | None = None) -> Reading:
    """READER 4 — the same decision, published in Kazakh as a separate file.

    **Its independence comes from reading a different file, not a different
    pattern.** Readers 1-3 all work over one Russian substring, so they share
    every failure of that substring: a corrupted extraction or a parser
    anchoring on the wrong number can take all three at once. A transcription
    slip cannot cross a translation, and two files cannot be corrupted the same
    way by accident.

    So it must never fall back to the Russian text. A missing Kazakh file is a
    refusal — which blocks confirmation — and that is the intended cost.

    Measured on all 19 documents of the 2026 set: the pair is present in every
    one, including both that the Russian readers cannot read at all.
    """
    # **A fetch failure is not a reading.** Measured: G25SI00331M lost its
    # Kazakh fetch to one transient TLS drop and was reported exactly like a
    # document whose Kazakh copy contradicts its Russian one — it re-fetched
    # cleanly moments later and reads 3. Both block confirmation, which is
    # right, but sending somebody to hunt a phrasing bug that does not exist is
    # not. The two are named differently from here on.
    if unavailable is not None:
        return Reading("kazakh", None, f"{UNAVAILABLE}{unavailable}", UNAVAILABLE_KIND)
    if kazakh_text is None:
        return Reading(
            "kazakh", None, f"{UNAVAILABLE}no Kazakh document was fetched", UNAVAILABLE_KIND
        )

    match = KAZAKH_PAIR.search(kazakh_text)
    if not match:
        return Reading("kazakh", None, "no `N пайыздан M пайыз` pair in the Kazakh text", NO_MATCH)

    old, old_word, new, new_word = match.groups()
    if int(new) >= int(old):
        return Reading("kazakh", None, f"not a понижение in Kazakh: {old} -> {new}", SUBSTANTIVE)

    # When the Kazakh spells the number too, it must agree with its own digit.
    # This is reader 4 checking itself inside its own file, and it is the same
    # trick the Russian pair gives readers 1 and 2 — which is why it can only
    # ever refuse here, never override.
    for digits, word in ((new, new_word), (old, old_word)):
        if word is None:
            continue
        spelled = KAZAKH_NUMERALS.get(word.strip().lower())
        if spelled is None:
            return Reading("kazakh", None, f"unknown Kazakh numeral {word.strip()!r}", SUBSTANTIVE)
        if spelled != int(digits):
            return Reading(
                "kazakh",
                None,
                f"Kazakh digit {digits} contradicts word {word.strip()!r}",
                SUBSTANTIVE,
            )

    return Reading("kazakh", int(new), match.group(0).strip())


def read_year(text: str) -> int | None:
    """The tax year, or None where the document names more than one.

    Refusing an ambiguous document is the point: a wrong year picks a wrong
    base rate, and the transition reader would then refuse a correct document
    or accept an incorrect one. Silence is recoverable; a confident wrong year
    is not.
    """
    found = {int(year) for pattern in YEAR_PATTERNS for year in re.findall(pattern, text)}
    return found.pop() if len(found) == 1 else None


def read_year_from_in_force(text: str) -> int | None:
    """The tax year, taken from the date the decision enters into force.

    A separate, named reader — **never a quiet fallback inside `read_year`**.
    It reads a different sentence making a different claim: «вводится в
    действие с 01.01.2026» says when the act starts, and the tax year follows
    because these decisions are enacted per calendar year.

    That last step is a convention, so the year it yields is labelled
    `in-force-date` in the output and a reader can weigh it differently from a
    year the document states outright. What it buys is not the rows themselves
    — it is that the origin check becomes live for them, and a row recovered
    this way is only worth having because a real check then runs on it.

    It reads only 1 January. A decision in force from another date is not a
    plain calendar-year enactment and is left to the year-less path.

    **It derives the date from `read_in_force` rather than matching the phrasing
    again.** It used to carry its own copy of «вводится в действие», so when
    `read_in_force` learned Astana's «вступает в силу» this did not, and the
    capital still had no year. Two functions holding one phrasing list drift the
    moment either is taught something, and nothing goes red: the corpus simply
    keeps a hole wherever the newer wording appears.
    """
    in_force = read_in_force(text)
    return int(in_force[:4]) if in_force and in_force.endswith("-01-01") else None


def read_decision_ref(text: str) -> str | None:
    """The citation as the document writes it, for a human to look up."""
    match = re.search(r"(Решение [^.]*?маслихата[^.]*?от \d{1,2} \w+ \d{4} года № [\w\-/]+)", text)
    return match.group(1).strip() if match else None


def read_in_force(text: str) -> str | None:
    months = {
        "января": "01",
        "февраля": "02",
        "марта": "03",
        "апреля": "04",
        "мая": "05",
        "июня": "06",
        "июля": "07",
        "августа": "08",
        "сентября": "09",
        "октября": "10",
        "ноября": "11",
        "декабря": "12",
    }
    marker = r"(?:вводится\s+в\s+действие|вступает\s+в\s+силу)\s+с\s+"
    found = {
        f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        for m in re.finditer(marker + r"(\d{2})\.(\d{2})\.(\d{4})", text, re.IGNORECASE)
    }
    for m in re.finditer(marker + r"(\d{1,2})\s+(\w+)\s+(\d{4})\s+года", text, re.IGNORECASE):
        month = months.get(m.group(2).lower())
        if month:
            found.add(f"{m.group(3)}-{month}-{int(m.group(1)):02d}")
    return found.pop() if len(found) == 1 else None


def classify(
    text: str,
    year_hint: int | None = None,
    kazakh_text: str | None = None,
    kazakh_error: str | None = None,
) -> dict[str, Any]:
    """Read the document four ways, check the regime a fifth, and report the result.

    Returns the outcome and every reading. It never picks a winner: choosing
    between disagreeing readers is exactly the judgement this design refuses.
    The fifth reader (`read_regime`) never picks a rate either — it only
    objects when the sentence the other four agree on turns out to govern a
    different special regime entirely.
    """
    superseded = [marker for marker in SUPERSEDED_MARKERS if marker in text]
    sentence = rate_sentence(text)
    year = read_year(text)
    year_source: str | None = "tax-period" if year else None
    if year is None:
        year = read_year_from_in_force(text)
        year_source = "in-force-date" if year else None
    if year is None and year_hint:
        year, year_source = year_hint, "caller-hint"

    # Reader 4 reads a different file, so it has an answer even where the
    # Russian text defeats readers 1-3. It is reported in every outcome rather
    # than only in the ones it can confirm: a document the Russian side cannot
    # read, whose Kazakh copy states the rate plainly, is a fact somebody needs
    # to see. **Reporting it is not confirming it** — one reading is never a row.
    kazakh = read_kazakh(kazakh_text, kazakh_error)

    if superseded:
        return {
            "outcome": UNPARSED,
            "reason": f"document is amended or repealed: {superseded[0]!r}",
            "readings": [kazakh._asdict()],
            "sentence": sentence,
            "year": year,
            "year_source": year_source,
            "terminal": False,
        }
    if sentence is None:
        return {
            "outcome": UNPARSED,
            "reason": "no single sentence lowers a rate — none matched, or several did",
            "readings": [kazakh._asdict()],
            "sentence": None,
            "year": year,
            "year_source": year_source,
            "terminal": False,
        }

    readings = [
        read_digit(sentence),
        read_word(sentence),
        read_transition(sentence, year),
        kazakh,
        read_regime(sentence),
    ]
    values = {reading.rate_percent for reading in readings if reading.rate_percent is not None}
    result: dict[str, Any] = {
        "readings": [reading._asdict() for reading in readings],
        "sentence": sentence,
        "year": year,
        "year_source": year_source,
        "decision_ref": read_decision_ref(text),
        "in_force_from": read_in_force(text),
    }

    # The confirmation rule: a rate is confirmed by at least two agreeing
    # readings from two different sources, and readers taking their number from
    # the same substring count as ONE — two patterns over one clause share every
    # failure of that clause, whereas two languages in two files do not, because
    # a transcription slip cannot cross a translation. What follows is that rule,
    # in the order that keeps the strictest objection first.
    unreachable = [r for r in readings if r.kind == UNAVAILABLE_KIND]
    objections = [r for r in readings if r.kind == SUBSTANTIVE]
    sources = {READER_SOURCE[r.reader] for r in readings if r.rate_percent is not None}

    if unreachable:
        return {
            **result,
            "outcome": CONFLICT,
            "reason": (
                f"{unreachable[0].reader} could not be fetched — a transport failure, "
                f"NOT a disagreement. Re-run before reading this as a defect. "
                f"({unreachable[0].detail.removeprefix(UNAVAILABLE)})"
            ),
            "terminal": False,
        }
    if objections:
        # **A refusal is evidence, not silence** — but only a refusal that read
        # the document and objected. An earlier version treated every refusal
        # as silence and confirmed a rate RISE at 4%; treating every refusal as
        # an objection would be the opposite error, blocking a document merely
        # phrased in a way one reader does not know.
        #
        # **`terminal` marks the one objection shape nothing can "fix":** the
        # regime reader read the document correctly and it genuinely governs
        # the retail-tax regime, not the simplified declaration. Widening a
        # reader to make that case pass would be widening it to publish a
        # wrong regime — the exact defect this reader exists to catch. Every
        # OTHER objection shape (a rise, a wrong starting point, an unknown
        # numeral) is a real candidate for a parser fix, so only an
        # objection set made up entirely of `regime` readings is terminal.
        return {
            **result,
            "outcome": CONFLICT,
            "reason": "; ".join(f"{r.reader} objected: {r.detail}" for r in objections),
            "terminal": all(r.reader == "regime" for r in objections),
        }
    if not values:
        return {
            **result,
            "outcome": UNPARSED,
            "reason": "no reader produced a rate",
            "terminal": False,
        }
    if len(values) > 1:
        return {
            **result,
            "outcome": CONFLICT,
            "reason": f"readers disagree: {sorted(values)}",
            "terminal": False,
        }
    if len(sources) < 2:
        # One reading is never a row, however confident it looks.
        return {
            **result,
            "outcome": UNPARSED,
            "reason": (
                f"only one independent reading ({', '.join(sorted(sources))}) — a rate needs "
                f"two, from different sources"
            ),
            "terminal": False,
        }

    percent = values.pop()
    rate = percent / 100
    if not (RATE_MIN <= rate <= RATE_MAX):
        return {
            **result,
            "outcome": CONFLICT,
            "reason": f"rate {rate} is outside the statutory band {RATE_MIN}..{RATE_MAX}",
            "terminal": False,
        }
    return {**result, "outcome": CONFIRMED, "rate": rate}


def extract(document_id: str) -> dict[str, Any]:
    """Fetch one decision and classify it, recording what it was read from."""
    url, payload = cached_document(document_id)
    text = pdf_text(payload)

    # The Kazakh copy is a separate published file. A failure to fetch it is a
    # refusal by reader 4, never a fallback to the Russian text: falling back
    # would silently collapse four readings onto one substring.
    try:
        kazakh_url, kazakh_payload = cached_document(document_id, language="kaz")
        kazakh_text: str | None = pdf_text(kazakh_payload)
        kazakh_sha = hashlib.sha256(kazakh_payload).hexdigest()
        kazakh_error: str | None = None
    except (urllib.error.URLError, ssl.SSLError, ValueError, http.client.HTTPException) as error:
        kazakh_url, kazakh_text, kazakh_sha = None, None, None
        kazakh_error = f"{type(error).__name__}: {error}"

    return {
        "kazakh_pdf_url": kazakh_url,
        "kazakh_pdf_sha256": kazakh_sha,
        "document_id": document_id,
        "source_url": f"{BASE_URL}/rus/docs/{document_id}",
        "pdf_url": url,
        "pdf_sha256": hashlib.sha256(payload).hexdigest(),
        "pdf_bytes": len(payload),
        **classify(text, kazakh_text=kazakh_text, kazakh_error=kazakh_error),
    }


class WouldShrinkDataset(RuntimeError):
    """A write covering fewer documents than the file already holds."""


def write_outputs(
    results: list[dict[str, Any]], allow_shrink: bool = False
) -> tuple[int, int, int]:
    """Confirmed rows to one file, everything else to the queue the preset reads.

    **Neither file is `data/rates.csv`.** Extracted rows stay separate until a
    human has seen real extractions and a real conflict rate.

    **This write is wholesale, so a partial run destroys the rows it did not
    cover.** The natural next step after repairing a parser is to re-run the
    documents you repaired — which is exactly the call that wipes everything
    else, and the most likely caller is the unattended reconcile session.

    So a write covering fewer documents than the file already holds **refuses**
    and requires `--allow-shrink`. It does not merge: merging would turn a loud
    failure into a quiet one and hide a genuinely shrinking dataset, which is a
    real event that must stay visible. Same reasoning as `UnknownKato` being an
    exception rather than a `None`.
    """
    if EXTRACTED.exists() and not allow_shrink:
        existing = json.loads(EXTRACTED.read_text(encoding="utf-8")).get("rows", [])
        existing_ids = {row["document_id"] for row in existing}
        writing_ids = {result["document_id"] for result in results}
        missing = existing_ids - writing_ids
        if missing:
            raise WouldShrinkDataset(
                f"this run covers {len(writing_ids)} documents and the file holds "
                f"{len(existing_ids)}; {len(missing)} would be deleted, e.g. "
                f"{sorted(missing)[:3]}. Re-run the full discovered set, or pass "
                f"--allow-shrink if the dataset really is smaller."
            )
    confirmed = [r for r in results if r["outcome"] == CONFIRMED]
    queued = [r for r in results if r["outcome"] != CONFIRMED]

    EXTRACTED.write_text(
        json.dumps(
            {"extraction_method": "deterministic-readers", "rows": confirmed},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    QUEUE.write_text(
        json.dumps({"pending": queued}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    conflicts = sum(1 for r in queued if r["outcome"] == CONFLICT)
    return len(confirmed), conflicts, len(queued) - conflicts


def main() -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Extract rates from adilet.zan.kz decisions.")
    parser.add_argument("document_ids", nargs="+", help="e.g. G25ZA00249M")
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="permit a write that drops documents the previous run covered",
    )
    arguments = parser.parse_args()

    results = [extract(document_id) for document_id in arguments.document_ids]
    try:
        confirmed, conflicts, unparsed = write_outputs(results, arguments.allow_shrink)
    except WouldShrinkDataset as error:
        print(f"REFUSED: {error}")
        return 1

    for result in results:
        print(f"{result['outcome']:>9}  {result['document_id']}  {result.get('reason', '')}")
    print(f"\nconfirmed {confirmed}, conflict {conflicts}, unparsed {unparsed}")
    print(f"wrote {EXTRACTED.relative_to(REPO_ROOT)} and {QUEUE.relative_to(REPO_ROOT)}")
    # A conflict is a defect in the reader, not in the document. Exit non-zero
    # so it cannot pass unnoticed in a script.
    return 1 if conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
