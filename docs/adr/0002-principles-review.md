# ADR 0002 — Principles review

**Date:** 2026-08-12 · **Status:** accepted · **Reviewed:** the brief, the stack choice and the six-slice build order, against a general checklist of engineering principles for building software with automated agents.

Run before any implementation started, so a finding cost one file edit rather than a rewrite on top of finished work.

Principles carry the checklist's own two tiers: `[S]` for the foundational, reach-for-it-constantly ones, `[A]` for the strong and broadly applicable.

## What bit, and what changed

### The no-agent-writes guard was weaker than it read `[S] guardrails-as-environment`

`tests/test_pipeline.py` asserted that no script contained the substrings `RATES_CSV.open("w"` or `RATES_CSV.write_text`. **It passed for `open("data/rates.csv", "w")`** — the most obvious way to write the file. A guard that looks load-bearing and is not is worse than no guard, because the reviewer stops looking.

**Changed:** the check now walks the AST of every script in `scripts/`, resolves both `open(path, mode)` and `path.open(mode)`, and rejects any write-mode target naming the rates CSV. A second test drives it with five inputs, including one that must *not* fire, because a check that has only run in the quiet case is unopposed rather than tested.

**What it still cannot catch is written into its docstring:** a script that shells out, or one that assembles the path at runtime. Those are covered by review rather than by the test — `data/rates.csv` is the first file to read in any diff, because it is the one file no script may write and a one-line change there is easy to miss inside a large diff.

### Tension, resolved against the principle: no second agent evaluates the first

`[S] generator-evaluator-loop` says never let the builder review its own work — split generation and evaluation across two agents with separate context windows.

**Rejected here, deliberately.** `SPEC.md` §7.2 is right about this domain: two instances of the same model share training and blind spots, so when one produces a plausible wrong rate the other finds it plausible, because it is the number it would have produced. That is correlated redundancy, which reads as coverage and provides almost none.

**The evaluator is deterministic code plus a human, not a second model.** `validate.py` for what is checkable — the fraction, the band, the nine digits, the required citation. A person for what is not: whether the number matches the decision at the other end of `source_url`. The principle's intent is honoured; its mechanism is not, and the reason is that the artefact under review is a fact rather than a piece of code.

## What was already honoured, and where

| Principle | Where |
|---|---|
| `[S]` verifiability determines autonomy | Assembling the full ~200-row dataset is routed to the human, because an agent cannot verify it. The work was sliced along the verifiability frontier rather than by value ordering. |
| `[A]` code / agent / human authority split | Exact answers to `validate.py`; ambiguous name matching to the MCP server, if it is ever built; the irreversible call — is this rate true — to a person, in `verified_by`. |
| `[A]` vertical-slice tasking | The first slice was cut *against* `SPEC.md` §11's horizontal build order, precisely to avoid typing 200 rows before a validator exists. |
| `[A]` serial execution, read-only parallelism | Work is serialised rather than run in parallel. `data/rates.csv`, `dist/` and the Pages deploy are each writable by one writer at a time, with the reason recorded for each. |
| `[S]` context-file hygiene | The instructions a contributor must read before acting are kept to roughly 90 lines, holding only what causes damage if unread; the specification and the reference material are opened on demand. |
| `[A]` filesystem as agent state | Plans, decisions and generated data are files on disk. Nothing durable lives in a message. |
| `[A]` reliability over accuracy | This project's stated way of dying is staleness that still looks maintained, so the planned liveness canary — assert the reference document was fetched and parsed *before* any job may report "no changes" — is a reliability instrument rather than an accuracy one. |

## Not applied

`[S] error analysis — look at your data` and `[A] trace-driven harness debugging` bear on the change watcher, which does not exist yet and so has produced nothing to read. Revisit when it has a first month of runs behind it.
