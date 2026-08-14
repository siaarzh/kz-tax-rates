# kz-tax-rates

The income tax rate under Kazakhstan's simplified regime (СНР на основе упрощённой декларации), per district, with a link to the maslikhat decision that set it.

The base rate is 4% under НК РК art. 726. A local maslikhat can move it by up to half, so a district's real rate sits between 2% and 6%. Around 200 of them publish their own decision each November, scattered across the national legal database. This pulls them into one file.

**Not tax advice.** It's a machine-readable copy of published legal facts, each row linking to its source. It doesn't interpret anything and it won't tell you what to pay. Open the linked decision before you rely on a number.

## Live

| | |
|---|---|
| Lookup page | https://siaarzh.github.io/kz-tax-rates/ |
| JSON, all years | `https://cdn.jsdelivr.net/gh/siaarzh/kz-tax-rates@master/dist/rates.json` |
| JSON, one year | `.../dist/rates-2026.json` |
| CSV | [`data/rates.csv`](data/rates.csv) |

`@master`, not `@main`. That's this repository's default branch and jsDelivr takes the branch name literally. Don't fetch from `raw.githubusercontent.com` directly, it's rate-limited and isn't a CDN.

## What's in it, and what isn't

**148 districts** for 2026. There are roughly 200 that could publish a decision, so this is partial and says so: `dist/rates.json` carries its own coverage count on every build.

**A missing district is not a district paying the base rate.** It means we found no decision for it. Those two look identical in an empty row, and telling them apart is the hard part of this problem, not the easy part.

## How a rate gets in

Read out of the decision document itself, by code, never recalled by a model and never typed from memory.

Each act is published in Russian and in Kazakh as separate files. Independent readers work over both, and a rate is recorded only when readings from two different files agree. Disagreement is thrown away rather than resolved, because picking the more plausible number is how a wrong rate gets in wearing a real citation. Every row keeps the exact sentence it came from, so you can check it without trusting the parser.

**No person has read these decisions.** Every row's `extraction_method` says `deterministic-readers`, meaning a script read it by rule. There is no human verification step: what protects a row is the citation next to it, the two-reader agreement that confirmed it, and the check that every published row still matches what the parser currently concludes — open `source_url` and read the sentence yourself before relying on it.

Two things that bite everyone: rates are fractions, so `0.03` and never `3` or `"3%"`. КАТО codes are strings. Parse one as an integer and you lose a leading zero, which quietly hands you a different district's rate.

## Layout

| Path | What it is |
|---|---|
| `data/rates.csv` | The dataset. |
| `data/kato.csv` | The КАТО spine from stat.gov.kz. `data/kato.source.json` records where it came from and when. |
| `dist/` | Built from `data/`. Committed on purpose, because Pages and jsDelivr serve it straight from the repository. |
| `scripts/extract_rates.py` | Fetches a decision and reads a rate out of it, or refuses and says why. |
| `scripts/map_districts.py` | Attaches a decision to one КАТО code, or leaves it unattached and counts it. |
| `scripts/publish_rates.py` | Writes `data/rates.csv` from `data/mapped-rates.json`. The dataset's only writer. |
| `scripts/validate.py` | Blocking checks. Also names the checks it doesn't make. |
| `scripts/build.py` | CSV to JSON. Validates first; a failure writes nothing. |
| `docs/SPEC.md` | Data model, КАТО handling, ingestion, correctness rules, distribution. |

## Running it

```sh
python scripts/publish_rates.py  # regenerate data/rates.csv from data/mapped-rates.json
python scripts/validate.py       # check data/rates.csv
python scripts/build.py          # regenerate dist/
```

Gates: `pytest`, `ruff check scripts tests`, `ruff format --check scripts tests`, `mypy`.

Fetching hits `adilet.zan.kz` at a fixed rate with an honest User-Agent, and caches every document locally so a re-run costs nothing.

## Licence

MIT, see [`LICENSE`](LICENSE). It covers this compilation and the scripts. The decisions themselves are legal acts, not copyrightable, so the licence neither claims nor could claim anything over them.
