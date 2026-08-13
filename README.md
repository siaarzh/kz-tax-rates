# kz-tax-rates

The income tax rate under Kazakhstan's simplified regime (СНР на основе упрощённой декларации), for every administrative-territorial unit, by year, keyed on КАТО, with a citation to the maslikhat decision that set it.

The base rate is 4% under НК РК art. 726. A local maslikhat may move it by ±50%, so the real rate in a district is between 2% and 6%. Roughly 200 maslikhats publish a separate decision each November. This repository consolidates them.

**Not tax advice.** This is a machine-readable copy of published legal facts, each row carrying a link to its primary source. It does not interpret anything, it does not tell you what to pay, and a rate here is only as current as its `verified_at` date. Check the linked decision before you rely on a number.

## Status

**Partial, and it says so.** `data/rates.csv` holds 127 rows against an estimated ~200 maslikhats that issue a decision. `dist/rates.json` reports its coverage on every build and never claims to be complete — a table that looks finished while most districts are missing is the same failure as one that goes stale while looking maintained.

## Layout

| Path | What it is |
|---|---|
| `data/rates.csv` | The source of truth. Hand-curated, human-verified, edited only by a person. |
| `data/kato.csv` | The КАТО spine, generated from stat.gov.kz. `data/kato.source.json` records exactly where from. |
| `dist/` | Generated from `data/`: `rates.json`, `rates-<year>.json`, `index.html`, `llms.txt`. Committed on purpose — GitHub Pages and jsDelivr serve it from the repository. |
| `datapackage.json` | Generated. Frictionless metadata describing both CSVs. |
| `scripts/validate.py` | Deterministic checks. Blocking. Names the checks it does not yet make. |
| `scripts/build.py` | CSV to JSON. Validates first; a failure writes nothing. |
| `docs/SPEC.md` | The specification: data model, КАТО handling, ingestion, correctness rules, distribution. |

## Build it

```sh
python scripts/validate.py     # check data/rates.csv
python scripts/build.py        # regenerate dist/
```

Gates: `pytest`, `ruff check scripts tests`, `ruff format --check scripts tests`, `mypy`.

## The rule that matters

**Every rate is read out of the decision by a person.** `verified_by` is never an agent, no row exists without a `source_url`, and no script writes to `data/rates.csv`. A model cannot tell "I read this in the decision" from "this is what decisions usually say", and that failure is the exact one this dataset exists to correct.

Rates are stored as fractions — `0.03`, never `3`, never `"3%"`. КАТО codes are strings; parsing one as an integer drops a leading zero and silently returns another district's rate.

## Using the data

`dist/rates.json` holds every year; `dist/rates-<year>.json` holds one, with the same schema. Both state `licence` and the "not tax advice" notice as fields, so a consumer reading only the JSON still gets the terms. `dist/llms.txt` describes the schema in plain text, and `datapackage.json` is [frictionless](https://specs.frictionlessdata.io/) metadata for both CSVs.

**Nothing is fetchable yet, and the remote existing does not change that.** `github.com/siaarzh/kz-tax-rates` is **private**: GitHub Pages serves nothing from it and the jsDelivr URL below returns 404, exactly as an invented URL would. It becomes real when the repository is made public, which is a decision, not a step.

```
https://cdn.jsdelivr.net/gh/siaarzh/kz-tax-rates@master/dist/rates.json
```

`@master`, not `@main` — that is this repository's default branch, and jsDelivr resolves the branch name literally. `raw.githubusercontent.com` is rate-limited and is not a CDN, which is why jsDelivr fronts it.

A custom domain, if one is ever used, must not be `.kz`: a `.kz` domain may be suspended when its resources are hosted outside Kazakhstan, and GitHub Pages is not hosted there.

## Licence

MIT — see [`LICENSE`](LICENSE). **It covers this compilation and the scripts, and nothing else.** The maslikhat decisions the data is read from are legal acts and are not copyrightable, so the licence neither claims nor could claim anything over them.
