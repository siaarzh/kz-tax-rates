"""data/rates.csv -> dist/rates.json and dist/rates-<year>.json.

Validation runs first and a failure writes nothing. CSV is the source of truth
and JSON is generated from it in CI, so the two cannot drift (SPEC.md §4).
"""

from __future__ import annotations

import base64
import csv
import html
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validate import KATO_CSV, RATES_CSV, REPO_ROOT, validate_file

DIST = REPO_ROOT / "dist"
SCRIPTS = Path(__file__).resolve().parent

# Places with no decision of their own that sit inside a jurisdiction that has
# one: villages, rural okrugs, city boroughs. They are searchable, never rows.
ALIASES_JSON = REPO_ROOT / "data" / "place-aliases.json"

# MiniSearch 7.0.0, MIT, vendored. Inlined into the page rather than fetched
# from a CDN, because the page must issue no request at all.
VENDOR_SEARCH = SCRIPTS / "vendor-minisearch.js"

SCHEMA_VERSION = "1.0"

# Kazakhstan has roughly 200 district-level councils (maslikhats) that issue a
# rate decision, so coverage is reported against that estimate. It is an
# estimate and it is published as one, so a partial dataset can never read as a
# complete one. The failure this guards against is a table that looks finished
# while most districts are missing.
ESTIMATED_DISTRICTS = 200

# The licence covers this compilation and the scripts. It does not and cannot
# cover the underlying decisions: a legal act is not copyrightable.
LICENCE = "MIT"

# Where this is actually served from. Every URL built out of these was fetched
# on 2026-08-13 and answered 200, including data/rates.csv and datapackage.json
# at the repository root: Pages publishes the whole tree, not dist/ alone.
#
# They live here rather than in the template for the same reason the disclaimer
# does. A location is a claim, and a URL nobody fetched is the same class of
# failure as a rate nobody read.
SITE = "https://siaarzh.github.io/kz-tax-rates/"
PAGE_URL = SITE + "dist/"
REPO_URL = "https://github.com/siaarzh/kz-tax-rates"

AUTHOR = "Serzhan Akhmetov"
AUTHOR_URL = "https://github.com/siaarzh"

# The SPDX page for the identifier, which is what a schema.org consumer
# dereferences. datapackage.json cites opensource.org because the frictionless
# packages read first cite it there; the two name the same licence.
LICENCE_URL = "https://spdx.org/licenses/MIT.html"

# The act the base rate comes from, cited so a reader can check the 4% for
# themselves. The identifier was confirmed by fetching it rather than guessed:
# the document at K2500000214 is «НАЛОГОВЫЙ КОДЕКС РЕСПУБЛИКИ КАЗАХСТАН», and
# scripts/extract_rates.py quotes article 726 of it for the same base rate.
#
# adilet.zan.kz serves a chain the default trust store does not carry, so a
# plain curl reports a connection failure rather than a 404. That is a local
# trust problem, not a dead link; scripts/adilet-chain.pem is what the pipeline
# already passes for it.
TAX_CODE_NAME = (
    "Налоговый кодекс Республики Казахстан от 18 июля 2025 года № 214-VIII ЗРК, статья 726"
)
TAX_CODE_URL = "https://adilet.zan.kz/rus/docs/K2500000214"

# extract_rates.py writes "deterministic-readers" into extraction_method for
# a row it read by rule. There is no human-verification tier any more: the
# citation, the deterministic reader and the cross-check against
# data/mapped-rates.json are what protect a row, not a person's name beside it.
#
# extraction_method values an automated reader actually writes. New methods
# must be added here explicitly, so a method nobody taught this set about
# reports honestly as not machine-extracted rather than silently passing.
MACHINE_EXTRACTION_METHODS = {"deterministic-readers"}

# Searchable in both languages, because the reader typing «упрощенка ставка
# район» and the crawler indexing "Kazakhstan tax rate dataset" are looking for
# the same file.
KEYWORDS = [
    "упрощённая декларация",
    "упрощёнка",
    "ставка налога",
    "СНР",
    "КАТО",
    "маслихат",
    "районы Казахстана",
    "налоги Казахстан",
    "открытые данные",
    "Kazakhstan",
    "simplified declaration regime",
    "income tax rate",
    "KATO classifier",
    "district tax rate",
    "open data",
]

# Must match --bg in the template's light and dark palettes. Two copies of one
# fact, so a test compares them and fails rather than letting the browser chrome
# drift away from the page it frames.
THEME_LIGHT = "#ffffff"
THEME_DARK = "#14161a"

# Carried in the JSON as a field, not only in the README, so a consumer reading
# the JSON alone still gets it. The line it keeps visible: tax consulting is a
# regulated activity in Kazakhstan, publishing structured public facts is not.
#
# Both languages, because the page is Russian and the JSON is read by people
# who are not.
NOT_TAX_ADVICE = (
    "Not tax advice. This is a machine-readable copy of published legal facts, "
    "each row citing its primary source. It interprets nothing and does not say "
    "what you owe. Read the linked decision before relying on a rate."
)
# «правовых фактов» was a calque colliding with «юридический факт», which means
# something else in Kazakh and Russian law, so the notice now names the acts.
NOT_TAX_ADVICE_RU = (
    "Не является налоговой консультацией. Это машиночитаемая копия сведений из "
    "опубликованных нормативных правовых актов; в каждой строке стоит ссылка на "
    "первоисточник. Здесь нет ни толкования норм, ни расчёта налога. Прежде чем "
    "полагаться на ставку, откройте само решение."
)


# Article 726 НК РК gives a maslikhat until 1 December of the preceding year to
# adopt the next year's rate, so a future year's column is partly decided and
# mostly not. It is said on the page only while a future column is drawn.
#
# Here rather than in the template, for the same reason the disclaimer is here:
# a sentence about what the data means is a claim, and the page must not be able
# to make one the build did not give it.
FUTURE_YEAR_NOTE_RU = (
    "Ставки на следующий год маслихаты принимают до 1 декабря. "
    "Пока эта дата не прошла, столбец заполнен лишь частично."
)


def generated_at() -> str:
    """Build time, overridable so an unchanged rebuild is byte-identical.

    dist/ is committed, because Pages and jsDelivr serve it from the repository,
    so an unpinned rebuild would dirty a generated file on every run.

    Pin it to the last commit that touched **data/**, never to HEAD:

        SOURCE_DATE_EPOCH=$(git log -1 --format=%at -- data/rates.csv) python scripts/build.py

    HEAD moves on documentation commits that cannot change the output, so
    pinning there leaves dist/ permanently dirty and trains everyone to ignore
    a modified generated file — which is where a real change then hides.
    """
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    stamp = datetime.fromtimestamp(int(epoch), UTC) if epoch else datetime.now(UTC)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_rows() -> list[dict[str, str]]:
    with RATES_CSV.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def oblast_names() -> dict[str, dict[str, str]]:
    """КАТО two-digit prefix -> the oblast's own names, read from the classifier.

    Derived rather than typed. The classifier already carries one row per oblast
    at `level == "oblast"`, so a hand-written prefix table would be a second copy
    of a fact we already hold — and two copies of one fact drift apart in silence.

    Why it matters at all: without an oblast on the row, searching «алматы»
    returns the city alone and every district of Алматинская область looks
    absent, which reads as a gap in the data rather than a gap in the search.
    """
    names: dict[str, dict[str, str]] = {}
    if not KATO_CSV.exists():
        return names
    with KATO_CSV.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("level") == "oblast":
                names[row["kato"][:2]] = {"ru": row["name_ru"], "kk": row["name_kk"]}
    return names


def build(rows: list[dict[str, str]]) -> dict[str, Any]:
    years: dict[str, dict[str, Any]] = {}
    kato_versions = {row["kato_version"] for row in rows}
    oblasts = oblast_names()

    for row in rows:
        year = row["valid_from"][:4]
        bucket = years.setdefault(year, {"base_rate": float(row["base_rate"]), "rates": []})
        oblast = oblasts.get(row["kato"][:2], {})
        bucket["rates"].append(
            {
                "kato": row["kato"],
                "name_ru": row["name_ru"],
                "name_kk": row["name_kk"],
                "oblast_ru": oblast.get("ru", ""),
                "oblast_kk": oblast.get("kk", ""),
                "rate": float(row["rate"]),
                "decision_ref": row["decision_ref"],
                "source_url": row["source_url"],
            }
        )

    for bucket in years.values():
        bucket["rates"].sort(key=lambda entry: entry["kato"])
        bucket["coverage"] = {
            "districts": len(bucket["rates"]),
            "estimated_total": ESTIMATED_DISTRICTS,
            "complete": False,
        }

    # Counted, never asserted. A consumer reading rates.json alone cannot see
    # extraction_method (it is a CSV column, not part of a rate entry), so
    # without this they would have no way to tell how a row was obtained.
    #
    # machine_extracted is read from extraction_method itself, never inferred:
    # a method never added to MACHINE_EXTRACTION_METHODS publishes as NOT
    # machine-extracted, so a new extraction path has to be taught to this set
    # explicitly rather than being counted for free.
    #
    # Additive and top level, so schema_version stays 1.0: a consumer reading
    # the keys it already knows is unaffected.
    machine = sum(
        1
        for row in rows
        if (row.get("extraction_method") or "").strip() in MACHINE_EXTRACTION_METHODS
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "kato_version": sorted(kato_versions)[0] if kato_versions else None,
        "licence": LICENCE,
        "not_tax_advice": NOT_TAX_ADVICE,
        "not_tax_advice_ru": NOT_TAX_ADVICE_RU,
        "verification": {
            "rows": len(rows),
            "machine_extracted": machine,
        },
        "years": years,
    }


def page_view(payload: dict[str, Any]) -> dict[str, Any]:
    """What the page needs to draw one year, or several, decided here.

    The multi-year layout turns itself on with the data: one year draws exactly
    the page it draws today, with no year column and nothing hinting that one
    exists. Two or more draw a column each, oldest to newest.

    `current_year` comes from `generated_at`, never from the reader's clock. A
    rebuild of last year's data has to produce last year's page; reading the
    clock instead would silently re-rank the columns of an archived build and
    make the tests depend on the day they run.

    It is a view of the payload and is inlined into the page alone. It is
    deliberately not a field of the payload: dist/rates.json is a published
    schema with consumers, and presentation is not part of it.
    """
    years = sorted(payload["years"])
    current = str(payload["generated_at"])[:4]
    future = [year for year in years if year > current]
    return {
        "years": years,
        "current_year": current,
        # Empty unless a future column is actually drawn. With one year there is
        # no column to qualify, and with no future year there is nothing partly
        # decided to warn about.
        "future_note_ru": FUTURE_YEAR_NOTE_RU if len(years) > 1 and future else "",
        # Who made this and where it lives, so a reader never has to infer it
        # from the URL bar. Passed through the view for the same reason the
        # licence is passed through the payload: the template states nothing it
        # was not given.
        "author": AUTHOR,
        "repo_url": REPO_URL,
        "provenance_ru": provenance_ru(payload),
        # [label, relative href, hover title] for the machine-readable files,
        # shown near the top of the page and not only in the metadata.
        "files": [[file["name"], file["href"], file["title"]] for file in artefacts(payload)],
    }


def provenance_ru(payload: dict[str, Any]) -> str:
    """How the rates in this build got here, counted from extraction_method.

    Counted rather than declared, so the sentence changes by itself the day a
    new extraction method is added, instead of standing as a stale claim.
    """
    counts = payload["verification"]
    if not counts["rows"]:
        return ""
    if counts["machine_extracted"] < counts["rows"]:
        return (
            "Часть ставок получена методом, который здесь не описан. Смотрите extraction_method "
            "в data/rates.csv для каждой строки."
        )
    return (
        "Ставки извлечены из решений маслихатов программой, по одному и тому же правилу. "
        "Прежде чем полагаться на ставку, откройте решение по ссылке рядом с ней."
    )


def districts(payload: dict[str, Any]) -> int:
    """Distinct КАТО codes across every year, not the sum of the year counts.

    Summing would double count a district we hold two years for and publish a
    coverage figure larger than the country.
    """
    return len({rate["kato"] for bucket in payload["years"].values() for rate in bucket["rates"]})


def page_title(payload: dict[str, Any]) -> str:
    """The title, built from the years present rather than typed with one in it.

    It used to carry a hardcoded 2026, which a second year of data would have
    left standing and wrong.
    """
    years = sorted(payload["years"])
    span = ", " + " · ".join(years) if years else ""
    return f"Ставка налога по упрощённой декларации (упрощёнка) по районам Казахстана{span} · КАТО"


def description_ru(payload: dict[str, Any]) -> str:
    """What a person searching for their own district's rate needs to read.

    It states a count and a scope and no rate at all: a description is a place
    a number would be copied from without its citation.
    """
    if not payload["years"]:
        return (
            "Бесплатный машиночитаемый справочник ставок налога по упрощённой декларации "
            "для районов и городов Казахстана. В каждой строке стоит ссылка на решение "
            "маслихата, из которого взята ставка."
        )
    years = sorted(payload["years"])
    span = years[0] if len(years) == 1 else f"{years[0]}, {years[-1]}"
    return (
        f"Ставка налога по упрощённой декларации («упрощёнка») по районам и городам "
        f"Казахстана за {span} год: {districts(payload)} районов и городов, поиск по названию "
        f"или коду КАТО. В каждой строке стоит ссылка на решение маслихата, из которого взята "
        f"ставка. Бесплатно, в JSON и CSV."
    )


def artefacts(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Every file this build publishes, with the URL that actually serves it.

    One list, read by three consumers: the schema.org distribution array, the
    links the page shows near the top, and llms.txt. Three hand-kept copies of
    this list would disagree within a release, and the one that disagreed would
    be the one an agent fetched.

    `href` is relative because the page must carry no absolute link to itself:
    a copy opened from file:// has to keep working, and a page that hardcodes
    its own origin stops working the moment it is mirrored.
    """
    files = [
        {
            "name": "rates.json",
            "url": PAGE_URL + "rates.json",
            "href": "rates.json",
            "format": "application/json",
            "title": "Все годы в одном файле",
        }
    ]
    for year in sorted(payload["years"]):
        files.append(
            {
                "name": f"rates-{year}.json",
                "url": f"{PAGE_URL}rates-{year}.json",
                "href": f"rates-{year}.json",
                "format": "application/json",
                "title": f"Только {year} год, та же схема",
            }
        )
    files.append(
        {
            "name": "data/rates.csv",
            "url": SITE + "data/rates.csv",
            "href": "../data/rates.csv",
            "format": "text/csv",
            "title": "Источник истины, одна строка на район и период",
        }
    )
    files.append(
        {
            "name": "datapackage.json",
            "url": SITE + "datapackage.json",
            "href": "../datapackage.json",
            "format": "application/json",
            "title": "Frictionless: типы столбцов обеих таблиц",
        }
    )
    files.append(
        {
            "name": "llms.txt",
            "url": PAGE_URL + "llms.txt",
            "href": "llms.txt",
            "format": "text/plain",
            "title": "Описание схемы для машинного читателя",
        }
    )
    return files


def dataset_jsonld(payload: dict[str, Any]) -> dict[str, Any]:
    """schema.org Dataset, populated from the payload and never by hand.

    This is what Google Dataset Search indexes, and it is the one piece of
    metadata on this page with a channel of its own rather than a checkbox.

    Every field is derived: the years decide temporalCoverage, the build clock
    decides dateModified, and the artefact list decides the distributions. A
    field typed here would be a second copy of a fact the payload already holds,
    and the copy is the one that goes stale.
    """
    years = sorted(payload["years"])
    block: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Ставки налога по упрощённой декларации по районам Казахстана",
        "alternateName": "kz-tax-rates",
        "description": description_ru(payload) + " " + payload["not_tax_advice"],
        "url": PAGE_URL,
        "sameAs": REPO_URL,
        "identifier": REPO_URL,
        "license": LICENCE_URL,
        "isAccessibleForFree": True,
        "creator": {"@type": "Person", "name": AUTHOR, "url": AUTHOR_URL},
        "publisher": {"@type": "Person", "name": AUTHOR, "url": AUTHOR_URL},
        "dateModified": payload["generated_at"],
        "version": payload["schema_version"],
        "inLanguage": ["ru", "kk"],
        "spatialCoverage": {
            "@type": "Place",
            "name": "Казахстан",
            "address": {"@type": "PostalAddress", "addressCountry": "KZ"},
        },
        "keywords": KEYWORDS,
        "citation": {"@type": "Legislation", "name": TAX_CODE_NAME, "url": TAX_CODE_URL},
        # The page never claims completeness and neither does its metadata.
        "creativeWorkStatus": "Incomplete",
        "distribution": [
            {
                "@type": "DataDownload",
                "name": file["name"],
                "description": file["title"],
                "encodingFormat": file["format"],
                "contentUrl": file["url"],
            }
            for file in artefacts(payload)
        ],
    }
    if years:
        # Closed on both sides, because the rows carry valid_to and a year of
        # this dataset is a year, not a period still running.
        block["temporalCoverage"] = f"{years[0]}-01-01/{years[-1]}-12-31"
    return block


def website_jsonld(payload: dict[str, Any]) -> dict[str, Any]:
    """WebSite with a SearchAction, declared only because ?q= actually works.

    The page reads ?q= into the search box on load, so the URL template below
    resolves to a real filtered page. Declaring one the page did not honour
    would send a reader to an unfiltered table and call it a search.
    """
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "url": PAGE_URL,
        "name": page_title(payload),
        "inLanguage": "ru",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": PAGE_URL + "?q={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
    }


def favicon_data_uri() -> str:
    """dist/favicon.svg as a data: URI, or empty where it is not there.

    Measured rather than assumed: `<link rel="icon" href="favicon.svg">` is a
    second HTTP request, which the page's zero-request rule forbids however
    same-origin it is. A data: URI is not a request at all, and 453 bytes of
    hand-drawn SVG costs about 620 characters inlined.

    Base64 rather than percent-encoded, because the file carries both `#` and
    double quotes and one of them ends the attribute early.

    Missing file yields no tag at all. A favicon pointing at nothing is the same
    failure as an og:image pointing at nothing.
    """
    icon = DIST / "favicon.svg"
    if not icon.exists():
        return ""
    return "data:image/svg+xml;base64," + base64.b64encode(icon.read_bytes()).decode("ascii")


def _script_safe(block: dict[str, Any]) -> str:
    """JSON for a <script> element: `</` cannot be allowed to close the tag."""
    return json.dumps(block, ensure_ascii=False, indent=2).replace("</", "<\\/")


def render_head(payload: dict[str, Any]) -> str:
    """Everything in <head> that a crawler, an agent or a share card reads.

    Built here rather than written into the template, because all of it is
    derived from the payload: the title carries the years, the description
    carries the count, and the structured data carries both plus the artefact
    list. A template can hold none of that without holding a stale copy of it.
    """
    title = page_title(payload)
    description = description_ru(payload)
    icon = favicon_data_uri()

    tags = [
        f"<title>{html.escape(title)}</title>",
        f'<meta name="description" content="{html.escape(description, quote=True)}">',
        f'<link rel="canonical" href="{PAGE_URL}">',
        f'<meta name="author" content="{html.escape(AUTHOR)}">',
        f'<meta name="theme-color" content="{THEME_LIGHT}" media="(prefers-color-scheme: light)">',
        f'<meta name="theme-color" content="{THEME_DARK}" media="(prefers-color-scheme: dark)">',
        f'<meta property="og:title" content="{html.escape(title, quote=True)}">',
        f'<meta property="og:description" content="{html.escape(description, quote=True)}">',
        f'<meta property="og:url" content="{PAGE_URL}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:site_name" content="kz-tax-rates">',
        '<meta property="og:locale" content="ru_RU">',
        '<meta property="og:locale:alternate" content="kk_KZ">',
        '<meta name="twitter:card" content="summary">',
        # No og:image and no twitter:image. A social preview image has not been
        # drawn, and a tag pointing at a file that does not exist is worse than
        # no tag: the card renders broken instead of rendering plain.
    ]
    if icon:
        tags.append(f'<link rel="icon" type="image/svg+xml" href="{icon}">')
    for block in (dataset_jsonld(payload), website_jsonld(payload)):
        tags.append('<script type="application/ld+json">\n' + _script_safe(block) + "\n</script>")
    return "\n".join(tags)


def read_aliases() -> list[list[str]]:
    """[kato, name_ru, name_kk, resolves_to_kato] for places with no decision.

    An alias is searchable and is never a row: the page states the jurisdiction
    whose decision covers it. Missing file yields an empty index rather than a
    build failure, because the aliases improve search and carry no rate.
    """
    if not ALIASES_JSON.exists():
        return []
    loaded: list[list[str]] = json.loads(ALIASES_JSON.read_text("utf-8"))
    return loaded


def render_index(payload: dict[str, Any]) -> str:
    """The lookup page, with the dataset, the aliases and the search inlined.

    SPEC.md §8.1. Inlined rather than fetched, so the page needs no request at
    all: it works from file://, from Pages and from a copy on a laptop with no
    network. A fetch would also fail silently under file:// and leave an empty
    table that looks like a complete one.

    `</script>` inside a payload would end the tag early, so the sequence is
    escaped in both JSON blobs. It cannot occur in either today, and a check
    that only holds while the data stays convenient is not a check. The vendor
    JavaScript cannot be escaped that way, so the same sequence there stops the
    build instead of producing a page that silently loses its search.

    One pass over the three placeholders, not three passes: a second pass would
    scan the text just inlined, and whichever blob went in first would have its
    own contents read as a placeholder.
    """
    template = (SCRIPTS / "index.template.html").read_text("utf-8")
    search = VENDOR_SEARCH.read_text("utf-8") if VENDOR_SEARCH.exists() else ""
    if "</" in search:
        raise SystemExit(f"{VENDOR_SEARCH.name} contains '</' and cannot be inlined safely")

    parts = {
        "/*DATA*/": json.dumps(payload, ensure_ascii=False, indent=2).replace("</", "<\\/"),
        "/*ALIASES*/": json.dumps(
            read_aliases(), ensure_ascii=False, separators=(",", ":")
        ).replace("</", "<\\/"),
        "/*VIEW*/": json.dumps(page_view(payload), ensure_ascii=False, indent=2).replace(
            "</", "<\\/"
        ),
        "/*MINISEARCH*/": search,
        # The head is HTML rather than a script body, so it needs an HTML
        # comment as its placeholder. Same single pass as the rest.
        "<!--HEAD-->": render_head(payload),
    }
    return re.sub(
        r"/\*(?:DATA|ALIASES|VIEW|MINISEARCH)\*/|<!--HEAD-->",
        lambda match: parts[match.group(0)],
        template,
    )


# Frictionless field types for the two CSVs. Read out of three real
# open-data-kazakhstan packages first (gdp-per-capita-by-regions,
# decent_work_indicators, covid-19-kz) rather than assumed: they use
# `tabular-data-package`, a `licenses` array of {name, path, title}, and
# resources carrying `path` plus `schema.fields` of {name, type, format}.
#
# **The convention does not constrain our columns.** Their datasets happen to
# use a long region/date/value shape, but frictionless prescribes types, not
# names. The open question — whether adopting the convention would force a
# column change — resolves as: no conflict, no column change.
#
# The types are metadata and cannot be derived from the CSV, so they are
# written here — and a test asserts the names match the two FIELDS lists, so
# adding a column without typing it fails rather than passing silently.
RATES_FIELDS: list[dict[str, Any]] = [
    {"name": "kato", "type": "string", "constraints": {"pattern": r"^\d{9}$"}},
    {"name": "kato_version", "type": "string"},
    {"name": "name_ru", "type": "string"},
    {"name": "name_kk", "type": "string"},
    # A fraction, never a percentage. The band is the statutory 0.04 base moved
    # by the ±50% a maslikhat may apply (НК РК art. 726).
    {"name": "rate", "type": "number", "constraints": {"minimum": 0.02, "maximum": 0.06}},
    {"name": "base_rate", "type": "number"},
    {"name": "valid_from", "type": "date"},
    {"name": "valid_to", "type": "date"},
    {"name": "decision_ref", "type": "string"},
    {"name": "source_url", "type": "string", "format": "uri", "constraints": {"required": True}},
    # How the number was obtained. Always set — every row has a method.
    {"name": "extraction_method", "type": "string", "constraints": {"required": True}},
]

KATO_FIELDS: list[dict[str, Any]] = [
    {"name": "kato", "type": "string", "constraints": {"pattern": r"^\d{9}$"}},
    {"name": "kato_version", "type": "string"},
    {"name": "level", "type": "string"},
    {"name": "type_code", "type": "string"},
    {"name": "name_ru", "type": "string"},
    {"name": "name_kk", "type": "string"},
]


def datapackage() -> dict[str, Any]:
    """Frictionless metadata for the two CSVs (SPEC.md §3)."""
    return {
        "name": "kz-tax-rates",
        "title": "Kazakhstan simplified-regime tax rate by district, keyed on КАТО",
        "description": NOT_TAX_ADVICE,
        "profile": "tabular-data-package",
        "licenses": [
            {
                "name": "MIT",
                "path": "https://opensource.org/licenses/MIT",
                "title": "MIT License · covers this compilation and the scripts, "
                "not the underlying legal acts, which are not copyrightable",
            }
        ],
        "resources": [
            {
                "name": "rates",
                "path": "data/rates.csv",
                "profile": "tabular-data-resource",
                "format": "csv",
                "encoding": "utf-8",
                "title": "One rate per district per validity period, each citing its decision",
                "schema": {
                    "fields": RATES_FIELDS,
                    "primaryKey": ["kato", "kato_version", "valid_from"],
                },
            },
            {
                "name": "kato",
                "path": "data/kato.csv",
                "profile": "tabular-data-resource",
                "format": "csv",
                "encoding": "utf-8",
                "title": "КАТО classifier, from stat.gov.kz · see data/kato.source.json",
                "schema": {"fields": KATO_FIELDS, "primaryKey": ["kato", "kato_version"]},
            },
        ],
    }


def render_llms_txt(payload: dict[str, Any]) -> str:
    """Plain-text description of the dataset for a machine reader (SPEC.md §3).

    Written for an agent that will fetch one URL and act on what it gets, so it
    leads with the URLs and states the two traps before the schema. Short enough
    to be read in full, because a file an agent skims is a file whose warnings
    are the part that gets skimmed.

    The repository is public and every URL below answered 200 on 2026-08-13.
    The earlier NOT FETCHABLE warning was true of a private remote and is now
    false, which is its own kind of wrong fact.

    Note `@master`. SPEC.md §8.2 writes `@main`; this repository's default
    branch is `master`, and jsDelivr resolves the branch literally.
    """
    years = sorted(payload["years"])
    counts = payload["verification"]
    files = artefacts(payload)
    width = max((len(file["url"]) for file in files), default=0)

    lines = [
        "# kz-tax-rates",
        "",
        "Income tax rate under Kazakhstan's simplified regime (СНР на основе упрощённой "
        "декларации), per administrative-territorial unit, by year, keyed on КАТО.",
        "",
        f"{payload['not_tax_advice']}",
        "",
        f"Licence: {payload['licence']} ({LICENCE_URL}), covering this compilation and the "
        "scripts. The underlying maslikhat decisions are legal acts and are not copyrightable, "
        "so no permission is needed to redistribute them.",
        "",
        "## Fetch",
        "",
        "Public, free, no key, no rate limit worth naming. Take the JSON.",
        "",
    ]
    lines += [f"{file['url'].ljust(width)}  {file['title']}" for file in files]
    lines += [
        "",
        "Mirror on jsDelivr, for volume. raw.githubusercontent.com is rate limited and is not",
        "a CDN. The branch is master, not main, and jsDelivr resolves it literally:",
        "",
        "https://cdn.jsdelivr.net/gh/siaarzh/kz-tax-rates@master/dist/rates.json",
        "",
        f"Source and issue tracker: {REPO_URL}",
        "",
        "## Schema of dist/rates.json",
        "",
        "schema_version, generated_at (UTC, ISO 8601), kato_version, licence,",
        "not_tax_advice, not_tax_advice_ru, verification, and years (an object keyed by year).",
        "",
        "verification holds rows and machine_extracted.",
        "Each year holds base_rate, rates[], and coverage {districts, estimated_total, complete}.",
        "Each entry of rates[] holds kato, name_ru, name_kk, oblast_ru, oblast_kk, rate,",
        "decision_ref, source_url.",
        "",
        "dist/rates-<year>.json is the same object with years narrowed to one key.",
        "",
        "## Two ways to read this wrong",
        "",
        "- rate and base_rate are FRACTIONS: 0.03 means 3%. Never a percentage, never a string.",
        "  Multiply by 100 to display. Storing 3 where 0.03 belongs is the error this dataset",
        "  is validated against, because it has already happened to someone.",
        "- kato is a STRING of nine digits. Parsing it as an integer drops a leading zero,",
        "  shortens the code, and silently returns a different district. No error is raised.",
        "",
        "## What an absent district means",
        "",
        "It means nobody has read that district's decision yet. It does NOT mean the district",
        "charges the base rate, and it does NOT mean the district has no rate. coverage.complete",
        "is false and stays false while districts < estimated_total. Do not fill a gap with",
        "base_rate: a maslikhat may lower the rate by up to half, so the gap is unknown, not 4%.",
        "",
        "## Provenance",
        "",
        "Every row carries source_url, a link to the maslikhat decision the rate was read from,",
        "a decision_ref naming that decision, and an extraction_method saying how the number was",
        "obtained. No rate is invented, inferred or filled in: every row traces to a specific",
        "sentence in a specific published decision, and disagreement between independent readers",
        "is thrown away rather than resolved.",
        "",
        f"Rows: {counts['rows']}. "
        f"Extracted from the decision by rule: {counts['machine_extracted']}.",
        "",
        "A machine-extracted row was parsed out of the published decision text by",
        "scripts/extract_rates.py, not produced by a language model, and extraction_method says",
        "so. Open source_url and read the sentence yourself before relying on the row.",
        "",
        f"Base rate and the ±50% a maslikhat may apply: {TAX_CODE_NAME}",
        f"{TAX_CODE_URL}",
        "",
        f"Years present: {', '.join(years) if years else 'none yet, the dataset is empty'}.",
        f"Built: {payload['generated_at']}.",
        "",
    ]
    return "\n".join(lines)


def render_sitemap(payload: dict[str, Any]) -> str:
    """A sitemap listing every published artefact, generated from `artefacts()`.

    Written rather than hand-maintained because a hand-written one had the
    namespace wrong (`w3.org/1999/sitemap/0.9` instead of
    `sitemaps.org/schemas/sitemap/0.9`) and listed three of six files. A crawler
    does not report either fault; it simply parses nothing and moves on.
    """
    urls = [SITE + "dist/"] + [SITE + item["url"].removeprefix(SITE) for item in artefacts(payload)]
    seen: list[str] = []
    for url in urls:
        if url not in seen:
            seen.append(url)
    body = "\n".join(
        f"  <url><loc>{url}</loc><lastmod>{payload['generated_at'][:10]}</lastmod></url>"
        for url in seen
    )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</urlset>\n'


def render_robots() -> str:
    """robots.txt, generated so its one honest caveat stays attached to it.

    **A project site cannot serve robots.txt.** GitHub Pages honours only
    `siaarzh.github.io/robots.txt`, which belongs to a different repository, so
    this file is documentation until a custom domain exists. Saying so here
    stops the next reader assuming crawl rules are in force that are not.
    """
    return (
        "# kz-tax-rates: a free, cited table of Kazakhstan's simplified-regime\n"
        "# tax rate per district. Crawl it, index it, take the data.\n"
        "#\n"
        "# NOT SERVED AS ROBOTS.TXT TODAY. This is a GitHub Pages project site,\n"
        "# so only siaarzh.github.io/robots.txt is honoured and that path belongs\n"
        "# to another repository. This file becomes live if a custom domain is\n"
        "# ever added. Submit the sitemap directly to a search console instead.\n"
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {SITE}dist/sitemap.xml\n"
    )


def write(payload: dict[str, Any]) -> list[Path]:
    DIST.mkdir(exist_ok=True)
    (DIST / "index.html").write_text(render_index(payload), encoding="utf-8")
    (DIST / "llms.txt").write_text(render_llms_txt(payload), encoding="utf-8")
    (DIST / "sitemap.xml").write_text(render_sitemap(payload), encoding="utf-8")
    (DIST / "robots.txt").write_text(render_robots(), encoding="utf-8")
    # At the repository root, not in dist/: it describes data/, and the
    # frictionless convention puts it beside the data it documents.
    (REPO_ROOT / "datapackage.json").write_text(
        json.dumps(datapackage(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written = [
        REPO_ROOT / "datapackage.json",
        DIST / "index.html",
        DIST / "llms.txt",
        DIST / "sitemap.xml",
        DIST / "robots.txt",
        DIST / "rates.json",
    ]
    (DIST / "rates.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for year, bucket in payload["years"].items():
        path = DIST / f"rates-{year}.json"
        single = {**payload, "years": {year: bucket}}
        path.write_text(json.dumps(single, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    errors = validate_file()
    if errors:
        for error in errors:
            print(f"INVALID {error}")
        print("validation failed — nothing written")
        return 1

    payload = build(read_rows())
    written = write(payload)
    for path in written:
        print(f"wrote {path.relative_to(REPO_ROOT)}")
    for year, bucket in payload["years"].items():
        print(f"  {year}: {bucket['coverage']['districts']}/~{ESTIMATED_DISTRICTS} districts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
