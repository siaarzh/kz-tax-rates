# Changelog

## 0.1.1 — 2026-09-12

- `kazakh_source_url` now cites the Kazakh act page (`https://adilet.zan.kz/kaz/docs/<id>`). The `/files/pdf/...` links it replaced stopped resolving on the canonical host the same day; all 158 act pages were opened in a browser and render the decision text. The row is still gated on the Kazakh PDF the readers confirmed from.

## 0.1.0 — 2026-09-12

First tagged release. The dataset itself (158 districts) has been live since 2026-08-15.

- Fetch from `old.adilet.zan.kz`. On 2026-09-12 `adilet.zan.kz` became a JavaScript-only app that serves an empty shell to plain HTTP clients, and its `robots.txt` now disallows `/search` and `/api/`. The legacy server-rendered site still runs on the old host and the scripts read from it; citation URLs stay on the canonical host (`cite_url`).
- `enumerate_decisions.py` refuses instead of reporting emptiness: a page with no result count is recorded as a failed listing, and a sweep that enumerated nothing exits 1 without writing `data/enumerated-decisions.json`. Before this, the host change produced 0 of 11,990 documents with every check green.
## Earlier

- Scaffolded the repository: validation and build pipeline, four gates, planning substrate.
