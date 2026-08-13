# kz-tax-rates — Technical Specification

**Status:** Ready to implement
**Date:** 2026-08-12
**What this is:** the technical half — how the dataset is built. The why is the [README](../README.md): every free consolidated list of these rates is either paywalled, uncited prose, or an LLM reciting the 4% base rate that is wrong in most districts.

---

## 1. Scope

A public dataset of the simplified-regime (СНР на основе упрощённой декларации) income tax
rate for every administrative-territorial unit in Kazakhstan, by year, keyed on КАТО, with
a citation to the maslikhat decision that set it.

**In scope:** rate data, КАТО reference, static hosting, a local MCP server.

**Out of scope, deliberately:** billing, authentication, user accounts, API keys and tiers; a marketing site or pricing page; any interpretation, advice, or "what should I pay" calculator; and coverage beyond rate decisions — no МРП, no МЗП, no thresholds, no filing calendar — until the rate dataset has been correct for one full annual cycle. The advice layer is excluded because tax consulting is a regulated activity in Kazakhstan while publishing structured public facts is not. Do not blur that line.

## 2. Architecture

```
data.egov.kz API ──► scripts/fetch_kato.py ──► data/kato.csv
                                                    │
adilet.zan.kz ──► (manual, annual) ─────────────────┼──► data/rates.csv
                                                    │
                                              scripts/build.py
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ▼                               ▼
                            dist/rates.json                 dist/rates-{year}.json
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             GitHub Pages      jsDelivr CDN     mcp/ (npm, stdio)
```

No servers. No database. No hosting cost. Git is the database; GitHub Actions is the
scheduler; jsDelivr is the CDN.

## 3. Repository layout

Follows the `open-data-kazakhstan` convention (frictionless data spec) so the dataset is
legible to existing tooling and could be contributed upstream.

```
kz-tax-rates/
├── README.md
├── LICENSE                  # MIT
├── datapackage.json         # frictionless metadata
├── data/
│   ├── rates.csv            # canonical, hand-curated, source of truth
│   ├── kato.csv             # generated from data.egov.kz
│   └── aliases.csv          # human name → КАТО, for fuzzy lookup
├── dist/                    # generated; committed so Pages/jsDelivr can serve it
│   ├── rates.json
│   ├── rates-2026.json
│   └── index.html           # single-page lookup UI
├── scripts/
│   ├── fetch_kato.py
│   ├── build.py             # csv → json, runs all validations
│   ├── validate.py
│   └── watch_adilet.py      # change detection only; never writes rates
├── tests/
│   └── test_canary.py
├── mcp/                     # npm package, stdio server
└── .github/workflows/
    ├── build.yml            # on push: validate + build + deploy Pages
    └── watch.yml            # weekly cron: detect changes, open issue
```

## 4. Data model

### `data/rates.csv`

```csv
kato,kato_version,name_ru,name_kk,rate,base_rate,valid_from,valid_to,decision_ref,source_url,verified_by,verified_at
750000000,NK RK 11-2025,г. Алматы,Алматы қаласы,0.03,0.04,2026-01-01,2026-12-31,Решение маслихата г. Алматы №256 от 28.11.2025,https://adilet.zan.kz/rus/docs/...,serzhan,2026-08-12
```

| Field | Type | Notes |
|---|---|---|
| `kato` | string(9) | Primary key component. **String, not integer** — leading zeros and arithmetic both matter. |
| `kato_version` | string | Which КАТО edition the code belongs to. Non-negotiable; see §5. |
| `rate` | decimal | Stored as a **fraction** (`0.03`), never as `3` or `"3%"`. |
| `base_rate` | decimal | National base for that year. 0.04 for 2026. |
| `valid_from` / `valid_to` | ISO date | Decisions are enacted per calendar year. |
| `decision_ref` | string | Human-readable citation, verbatim from the act. |
| `source_url` | url | Direct link to the primary source. **Required.** No row without one. |
| `verified_by` | string | Who eyeballed the source. Never an agent. |
| `verified_at` | ISO date | When. |

Primary key: `(kato, kato_version, valid_from)`.

### `dist/rates.json`

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-08-12T14:00:00Z",
  "kato_version": "NK RK 11-2025",
  "years": {
    "2026": {
      "base_rate": 0.04,
      "rates": [
        {
          "kato": "750000000",
          "name_ru": "г. Алматы",
          "rate": 0.03,
          "decision_ref": "Решение маслихата г. Алматы №256 от 28.11.2025",
          "source_url": "https://adilet.zan.kz/rus/docs/..."
        }
      ]
    }
  }
}
```

Ship both CSV and JSON. Generate JSON from CSV in CI so they cannot drift.

## 5. КАТО handling — the subtle part

КАТО is a temporal key here, not a current-state lookup. It mutates:

- The classifier itself was revised (ГК РК 11-2009 → НК РК 11-2025).
- Kazakhstan created Abai, Jetisu, and Ulytau oblasts in 2022.
- The city of Alatau is new enough that its maslikhat only began issuing rate decisions
  for 2026.

A rate keyed on a code that was later reassigned returns silently wrong data.

**Decision: freeze on НК РК 11-2025 and maintain an explicit migration map.**

- Every row carries `kato_version`.
- `data/kato_migrations.csv` maps retired codes to current ones with an effective date.
- Lookups for a historical year resolve through the migration map.

This is less pure than versioning every code, and it keeps queries simple. Decide now —
retrofitting after three years of data is painful.

## 6. Ingestion

### 6.1 КАТО — automated

`data.egov.kz` exposes a REST API returning JSON/XML/Excel with typed field definitions.
Read access does not require registration; the registration and official-letter process
applies to *publishing* datasets.

`scripts/fetch_kato.py` pulls the classifier and writes `data/kato.csv`. Safe to automate.

### 6.2 Rates — ~~manual, annual, by design~~ SUPERSEDED 2026-08-12

> **Everything in this subsection is false and is kept only so that somebody who remembers the old rule can find out it changed.** The premise was tested and did not hold: `adilet.zan.kz` serves fine, its full-text search and issuing-body facets are usable as URL parameters, each decision is a PDF whose text extracts cleanly, and the rate is stated in that text. The only barrier was an incomplete TLS certificate chain.
>
> **Rates are read out of the decision documents by deterministic parsers.** A rate is confirmed only when two readings from two *different files* agree — the Russian text and the Kazakh text of the same act, which share no substring, no extraction, no PDF and no language, so a transcription slip cannot cross between them. Readers that take their number from the same substring count as **one** reading and do not confirm. The implementation and its full reasoning live in `scripts/extract_rates.py`.
>
> **§7 below is unchanged and still governs.** No agent supplies a rate; `verified_by` is a person.

`adilet.zan.kz` returns `ROBOTS_DISALLOWED` to automated fetches. No official API was
found. Rate decisions are NPAs and are not within the `data.egov.kz` open-data taxonomy,
which is organised around Education, Health, Transport, Statistics, Culture, Security.

**Therefore rates are entered by hand, once a year, in December.** ~200 rows, one
afternoon.

This is a deliberate choice, not a gap:

- The automation cost is high and the frequency is annual.
- Verification cannot be automated regardless (§7), and verification is most of the work.
- A hand-curated dataset that exists beats a scraper that is still in progress next
  November.

**Do not let the scraper block the dataset.** Ship the manual pipeline first.

### 6.3 Change detection — automated, advisory only

`scripts/watch_adilet.py` runs weekly. Its job is **diffing, not extraction**:

- Has anything new been published matching maslikhat rate-decision patterns?
- On a hit: open a **GitHub issue** with the URL and a proposed row.
- It **never** writes to `data/rates.csv` and **never** merges.

If polite scraping is attempted: 1 req/sec, honest User-Agent with a contact address,
cache aggressively. `robots.txt` is not law in KZ, but it is a stated preference — respect
the rate limit and be reachable.

## 7. Correctness — non-negotiable

An LLM authoring tax data reproduces exactly the failure mode this project exists to fix.
A model cannot distinguish "I read this in the decision" from "this is what decisions
usually say."

### 7.1 Human is the merge gate

Agents open PRs. Humans merge. `verified_by` is never an agent identifier.
Branch protection: require one approving review on `data/**`.

### 7.2 Do not use a second agent to verify the first

Two instances of the same model share training and blind spots. When one hallucinates a
plausible rate, the other finds it plausible — because it is the number it would have
produced. That is correlated redundancy, which reads as coverage on a dashboard and
provides almost none. Verifier agents also bias toward approval when handed a
plausible-looking artifact.

### 7.3 Canary test — deterministic

```python
# tests/test_canary.py
# A decision whose content is frozen and independently known.
CANARY = {
    "doc_id": "G25ZA00249M",       # Уральск, decision №24-9 of 28.11.2025
    "kato": "270000000",
    "expected_rate": 0.03,
}

def test_parser_still_works():
    parsed = fetch_and_parse(CANARY["doc_id"])
    assert parsed.rate == CANARY["expected_rate"], "PARSER BROKEN — halt, do not write"
```

Ten lines, deterministic, cannot be reasoned with or prompted into agreement. If adilet
reshuffles its markup, this fails and nothing gets written.

### 7.4 Liveness — distinguish "no change" from "could not check"

The worst failure is silent: the scrape returns empty, the job reports "no changes," and
the dataset freezes while every signal says healthy. That is strictly worse than an
abandoned repo, because abandoned repos *look* abandoned.

`watch.yml` must assert that the canary document was fetched and parsed successfully
before it is allowed to report "no changes." Failure to fetch → job fails loudly → issue
opened.

### 7.5 Schema validation

`scripts/validate.py`, blocking in CI:

- Every row has a non-empty `source_url` and `decision_ref`
- `rate` between 0.02 and 0.06 inclusive (statutory ±50% bound on the 0.04 base)
- `rate` is a fraction, not a percentage — reject anything > 1
- `kato` matches `^\d{9}$` and exists in `data/kato.csv`
- No overlapping `valid_from`/`valid_to` for the same `(kato, kato_version)`
- `verified_by` is not empty

The `rate > 1` check exists because the single most likely data-entry error is storing `3`
instead of `0.03`. This is not hypothetical — the exact bug appeared in the owner's own tax
spreadsheet, where a rate cell held the text `'3.00%'` instead of the number `0.03`.

## 8. Distribution

### 8.1 GitHub Pages

Serves `dist/`. Free TLS, ~100GB/mo bandwidth, 1GB limit. The dataset is ~200 rows —
four orders of magnitude inside the limit.

`dist/index.html` is a single self-contained file: КАТО/name lookup over the inlined
dataset, no backend, no framework. Every result displays the decision reference and a link
to the primary source.

### 8.2 jsDelivr

`raw.githubusercontent.com` is rate-limited and is not a CDN. Front it:

```
https://cdn.jsdelivr.net/gh/siaarzh/kz-tax-rates@master/dist/rates.json
```

### 8.3 Custom domain

**Do not use a `.kz` domain.** Under the `.kz` registration rules, a domain in the
Kazakhstan internet segment may be suspended when its resources are hosted outside the
territory of Kazakhstan — the cited example is a `.kz` domain pointing at EU-hosted pages,
which is suspended and then deleted. GitHub Pages is not hosted in Kazakhstan.

Enforcement in practice is unclear, but the rule is on the books and would bite roughly a
year in. Use `.dev`, `.org`, or `.io`.

### 8.4 `llms.txt`

Serve `/llms.txt` describing the schema and endpoints in plain text. Fifteen minutes of
work; gets most of the agent-accessibility benefit without anyone installing anything.

## 9. MCP server

**Build this last, and only if §9.1 still holds after the dataset ships.**

### 9.1 Does it earn its place?

If the data is a public URL, any agent with web fetch already has access. A stdio server
the user must install is friction. It is justified only by:

1. **Name normalisation.** Input is human: "Медеуский район", "Медеу", "Алматы",
   "Almaty", "Алматы облысы". The last is a *different tax jurisdiction* from Алматы
   қаласы and returns a different rate. A raw JSON dump makes the agent guess; a tool with
   fuzzy matching plus explicit disambiguation errors prevents a class of silent wrong
   answers.
2. **Tool descriptions are triggers.** An MCP tool description tells the model *when* to
   reach for the data. A URL in a README does not. That is the difference between the
   agent using the dataset and the agent confidently reciting 4% from training data.

### 9.2 Transport

**stdio, shipped as an npm package.** Not HTTP.

- Zero hosting, zero uptime obligation, works offline after first fetch.
- Fetches `dist/rates.json` from jsDelivr on startup, caches locally.
- One line in the user's MCP config to install.

For an annually-changing dataset this is clearly correct. An HTTP server on Workers or Deno
Deploy would add infrastructure that can go down, for no benefit.

### 9.3 Tools

```
get_rate(region: string, year?: number)
  → { kato, name, rate, base_rate, decision_ref, source_url, valid_from, valid_to }
  → on ambiguity, an error naming every candidate:
    "«Алматы» is ambiguous: г. Алматы (750000000, 3%) or
     Алматинская область (190000000, varies by district)"

list_rates(year: number)  → all rows for that year
get_kato(query: string)   → fuzzy name → КАТО candidates
```

Every response carries `source_url`. The server returns facts and citations. It does not
interpret, advise, or compute anyone's tax.

## 10. Automation

`.github/workflows/build.yml` — on push to `data/**`:
validate → build → test canary → deploy Pages. Any failure blocks.

`.github/workflows/watch.yml` — weekly cron:
canary liveness check → diff adilet → open an issue on a hit. Never commits.

Free tier on public repos is unlimited Actions minutes.

## 11. Build order

1. `data/kato.csv` from the data.egov.kz API — the spine
2. `data/rates.csv` — 2026 rates by hand, ~200 rows, one afternoon
3. `scripts/validate.py` + `scripts/build.py` + CI
4. `dist/index.html` + Pages + jsDelivr
5. `llms.txt`, README, MIT licence, "not tax advice" notice
6. `scripts/watch_adilet.py` + canary + weekly workflow
7. MCP server — only if §9.1 still holds

Steps 1–5 are the deliverable. Steps 6–7 are enhancements.

## 12. Notes for implementing agents

- **Never write a rate you inferred.** Every rate comes from a human reading the decision.
  If you cannot cite `source_url`, do not write the row.
- **Never merge your own PR.**
- Rates are fractions. `0.03`, never `3`, never `"3%"`.
- КАТО codes are strings. Never parse them as integers.
- Prefer boring, deterministic assertions over model judgement everywhere in the pipeline.
- If a check would be "ask a model whether this looks right," replace it with an assertion
  or remove it.
