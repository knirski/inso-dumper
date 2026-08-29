# AGENTS.md

Guidance for AI agents working in this repository.

## Project

**inso-dumper** — a local backup tool for the Inso platform (https://app.inso.pl), a
kindergarten/preschool parent-teacher communication app. It dumps announcements (with photo
sets), messages, and documents available to a parent account, per child, with deduplication
into a shared `_common/` area.

Read before doing any work:

- `docs/prd.md` — product requirements (storage layout, dedup rules, CLI contract).
- `docs/plan.md` — phased implementation plan with per-phase checks.
- `docs/api-notes.md` — Inso API discoveries (created in Phase 0; may not exist yet).

Status: planning/API-discovery phase. Do not invent API details; they must come from real
captured traffic (see plan.md Phase 0).

## Tooling — uv only

- The project is uv-managed. **All** Python invocations go through uv:
  `uv run inso-dumper …`, `uv run pytest`, `uv run ruff check .`.
- Never use bare `python`, `pip`, or `pipx`. Add deps with `uv add`; commit `uv.lock`.
- There is no Bash scripting convention in this repo; prefer Python run via uv.

## Repo layout

- `docs/` — lowercase file names (prd.md, plan.md, api-notes.md).
- `.agents/skills/<skill>/` — project skills (source of truth).
- `.agents/AGENTS.md` — rules for maintaining/updating those skills; read it before
  touching anything under `.agents/`.
- The future dump output directory (`dump/` by default) contains children's personal data —
  never commit it or anything derived from it.

## Conventions

- File names: lower case everywhere.
- Secrets (Inso credentials, session tokens) never enter the repo — use env vars
  (`INSO_EMAIL`/`INSO_PASSWORD`) or user-level config paths.
- Commits: conventional style, e.g. `docs: …`, `feat: …`, `fix: …`.
- Work is read-only with respect to the Inso platform: GET-only endpoints unless verified
  otherwise; never send messages or mark content as read as a side effect.
