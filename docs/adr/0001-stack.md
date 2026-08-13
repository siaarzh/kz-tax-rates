# ADR 0001 — Stack

**Date:** 2026-08-12 · **Status:** accepted · **Decided by:** the project scaffold, confirmed by the repository owner.

## Context

A public dataset of ~200 rows of tax-rate facts, each carrying a citation, generated from a hand-curated CSV into JSON, served statically. `SPEC.md` §2 already fixes the architecture: no servers, no database, git as the store, GitHub Actions as the scheduler, jsDelivr as the CDN. The stack question is only what the scripts and the gates are written in.

The first slice fetches the КАТО classifier over HTTP, reads and writes CSV and JSON, validates rows, and emits a static page.

## Decision

**Python 3.11+, standard library only** (`urllib`, `csv`, `json`, `pathlib`). Gates: `pytest`, `ruff check`, `ruff format --check`, `mypy`.

`pyproject.toml` carries tool configuration and nothing else. There is no `[project]` table and no build backend, because nothing here is installable or published to PyPI — the deliverable is the data.

## What it beat, and why

**Node with TypeScript**, which would share code with the MCP server in `SPEC.md` §9.

Python wins on the two error classes this project is most exposed to. `mypy` catches a КАТО handled as an `int` and a rate handled as a percentage at review time rather than at runtime, and both of those are silent-wrong-answer bugs rather than crashes. The shared-code argument for Node is weak because the MCP server is explicitly gated on §9.1 still holding after the dataset ships, and may never be built. When it is built it can be Node in its own directory, reading the published JSON like any other consumer — which is the interface it would use regardless.

The bar applied was not execution speed. It was how fast an agent with no context can read this repository, change it, and prove the change.

## Deliberately not scaffolded

Docker, a database, authentication, a logging framework, an ORM, a frontend framework, and a `[project]`/packaging table. Each is a real decision with its own slice. None was rejected on principle; they were rejected as premature. `SPEC.md` §2 rules most of them out permanently.

## Consequences

**Easier:** one language for the whole pipeline; four gates that run in under four seconds; a contributor needs only Python and no build step.

**Harder:** the MCP server, when and if it happens, will not share parsing code with the pipeline. It reads `dist/rates.json`, which is the published contract, so the duplication is confined to fuzzy name matching.

**Watch:** `dist/` is committed, so a non-deterministic build would produce a diff on every run. `build.py` honours `SOURCE_DATE_EPOCH` for exactly this reason, and CI must set it to the commit time.

## Amendment, 2026-08-12 — one dependency: `pypdf`

**"Standard library only" now has exactly one exception, and it was decided by measurement rather than by argument.**

Rates turned out to be extractable from the decision PDFs on `adilet.zan.kz`, which replaced the hand-entry design. Reading them needs a PDF text extractor. **Two** standard-library extractors were written and measured before the dependency was accepted, and **both silently corrupted the exact sentence the rate readers parse**:

```
4 (четырех) процентов   →   4 (четырех процентов
```

The cause is structural, not a bug to fix cheaply. A PDF literal string may contain balanced parentheses, and these documents encode text as two-byte CIDs in which `0x28`/`0x29` occur as ordinary data — so any byte-level scan ends the string early. Doing it correctly means a real object and cross-reference lexer.

**A lost bracket lands inside the number and nothing goes red.** That is the failure class this whole project exists to prevent, arriving through the parser rather than through a model. Removing it is worth more than the rule was protecting.

Confined to one function, `pdf_text(bytes) -> str`, and pinned. Swapping it later changes nothing else.

**This does not reopen the stack.** No web framework, no ORM, no build step; a contributor still needs only Python. The bar for the next dependency is the same one this cleared: a measured, silent, wrong-answer failure in the thing it would replace.

## Revisit when

The MCP server is actually being built and its name-normalisation logic turns out to need the same alias table the pipeline uses. That is the only condition under which one language for both stops being a preference and starts being a saving.
