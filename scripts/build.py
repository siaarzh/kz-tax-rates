"""data/rates.csv -> dist/rates.json and dist/rates-<year>.json.

Validation runs first and a failure writes nothing. CSV is the source of truth
and JSON is generated from it in CI, so the two cannot drift (SPEC.md §4).
"""

from __future__ import annotations

import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from validate import RATES_CSV, REPO_ROOT, validate_file

DIST = REPO_ROOT / "dist"

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

# Carried in the JSON as a field, not only in the README, so a consumer reading
# the JSON alone still gets it. The line it keeps visible: tax consulting is a
# regulated activity in Kazakhstan, publishing structured public facts is not.
#
# Both languages, because the page is Russian and the JSON is read by people
# who are not.
NOT_TAX_ADVICE = (
    "Not tax advice. This is a machine-readable copy of published legal facts, "
    "each row citing its primary source. It interprets nothing and does not say "
    "what you owe. A rate is only as current as its verified_at date — read the "
    "linked decision before relying on it."
)
NOT_TAX_ADVICE_RU = (
    "Не является налоговой консультацией. Это машиночитаемая копия опубликованных "
    "правовых фактов, каждая строка ссылается на первоисточник. Здесь нет "
    "толкования и нет расчёта того, сколько платить. Ставка актуальна на дату "
    "verified_at — перед использованием откройте само решение."
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


def build(rows: list[dict[str, str]]) -> dict[str, Any]:
    years: dict[str, dict[str, Any]] = {}
    kato_versions = {row["kato_version"] for row in rows}

    for row in rows:
        year = row["valid_from"][:4]
        bucket = years.setdefault(year, {"base_rate": float(row["base_rate"]), "rates": []})
        bucket["rates"].append(
            {
                "kato": row["kato"],
                "name_ru": row["name_ru"],
                "name_kk": row["name_kk"],
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

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at(),
        "kato_version": sorted(kato_versions)[0] if kato_versions else None,
        "licence": LICENCE,
        "not_tax_advice": NOT_TAX_ADVICE,
        "not_tax_advice_ru": NOT_TAX_ADVICE_RU,
        "years": years,
    }


def render_index(payload: dict[str, Any]) -> str:
    """The lookup page, with the dataset inlined (SPEC.md §8.1).

    Inlined rather than fetched, so the page needs no request at all: it works
    from file://, from Pages and from a copy on a laptop with no network. A
    fetch would also fail silently under file:// and leave an empty table that
    looks like a complete one.

    `</script>` inside the data would end the tag early, so the sequence is
    escaped. It cannot occur in this dataset today, and a check that only holds
    while the data stays convenient is not a check.
    """
    template = (Path(__file__).resolve().parent / "index.template.html").read_text("utf-8")
    data = json.dumps(payload, ensure_ascii=False, indent=2).replace("</", "<\\/")
    return template.replace("/*DATA*/", data)


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
    {"name": "verified_by", "type": "string", "constraints": {"required": True}},
    {"name": "verified_at", "type": "date"},
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
                "title": "MIT License — covers this compilation and the scripts, "
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
                "title": "КАТО classifier, from stat.gov.kz — see data/kato.source.json",
                "schema": {"fields": KATO_FIELDS, "primaryKey": ["kato", "kato_version"]},
            },
        ],
    }


def render_llms_txt(payload: dict[str, Any]) -> str:
    """Plain-text description of the dataset for a machine reader (SPEC.md §3).

    The remote exists and is **private**, so the URLs below fetch nothing. They
    are stated as what will serve the data once it is public, and labelled as
    not fetchable today — a URL that 404s reads as a fact, which is the failure
    this text existed to avoid when the account name was still unknown.

    Note `@master`. SPEC.md §8.2 writes `@main`; this repository's default
    branch is `master`, and jsDelivr resolves the branch literally.
    """
    years = sorted(payload["years"])
    lines = [
        "# kz-tax-rates",
        "",
        "Income tax rate under Kazakhstan's simplified regime (СНР на основе упрощённой "
        "декларации), per administrative-territorial unit, by year, keyed on КАТО.",
        "",
        f"{payload['not_tax_advice']}",
        "",
        f"Licence: {payload['licence']}, covering this compilation and the scripts. The "
        "underlying maslikhat decisions are legal acts and are not copyrightable.",
        "",
        "## Files",
        "",
        "- dist/rates.json — every year in one file.",
        "- dist/rates-<year>.json — one year, same schema.",
        "- dist/index.html — the lookup page, with the data inlined.",
        "- data/rates.csv — the source of truth, hand-verified, one row per district per period.",
        "- data/kato.csv — the КАТО spine from stat.gov.kz; data/kato.source.json records where.",
        "",
        "## Schema of dist/rates.json",
        "",
        "schema_version, generated_at (UTC, ISO 8601), kato_version, licence,",
        "not_tax_advice, not_tax_advice_ru, and years — an object keyed by year.",
        "",
        "Each year holds base_rate, rates[], and coverage {districts, estimated_total, complete}.",
        "Each entry of rates[] holds kato, name_ru, name_kk, rate, decision_ref, source_url.",
        "",
        "## Reading the numbers",
        "",
        "- rate and base_rate are FRACTIONS: 0.03 means 3%. Never a percentage, never a string.",
        "- kato is a STRING of nine digits. Parsing it as an integer drops a leading zero and",
        "  silently returns a different district.",
        "- coverage.complete is always false while districts < estimated_total. A district that",
        "  is absent has not had its decision read yet; it does not mean the district has no rate.",
        "",
        "## Provenance",
        "",
        "Every row carries source_url, a link to the decision it was read from, and verified_by,",
        "the person who read it. No rate is generated, inferred or filled in by a model.",
        "",
        f"Years present: {', '.join(years) if years else 'none yet — the dataset is empty'}.",
        f"Built: {payload['generated_at']}.",
        "",
        "## Published copies",
        "",
        "NOT FETCHABLE TODAY. github.com/siaarzh/kz-tax-rates is a PRIVATE repository, so",
        "GitHub Pages serves nothing and the jsDelivr URL below 404s. Nothing here is public.",
        "",
        "Once the repository is made public, Pages serves dist/ and jsDelivr fronts the raw",
        "files — raw.githubusercontent.com is rate-limited and is not a CDN:",
        "",
        "https://cdn.jsdelivr.net/gh/siaarzh/kz-tax-rates@master/dist/rates.json",
        "",
    ]
    return "\n".join(lines)


def write(payload: dict[str, Any]) -> list[Path]:
    DIST.mkdir(exist_ok=True)
    (DIST / "index.html").write_text(render_index(payload), encoding="utf-8")
    (DIST / "llms.txt").write_text(render_llms_txt(payload), encoding="utf-8")
    # At the repository root, not in dist/: it describes data/, and the
    # frictionless convention puts it beside the data it documents.
    (REPO_ROOT / "datapackage.json").write_text(
        json.dumps(datapackage(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written = [
        REPO_ROOT / "datapackage.json",
        DIST / "index.html",
        DIST / "llms.txt",
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
