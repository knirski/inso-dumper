# Plan: inso-dumper

Companion to [prd](prd.md). Ordered, verifiable steps. Each phase ends with a check.

## Phase 0 — API discovery (agent-browser)

1. `agent-browser open https://app.inso.pl/login` → `snapshot -i` to map the login form.
2. Ask the user to supply credentials via the agent-browser auth vault (password never lands in
   shell history):
   ```bash
   agent-browser auth save inso --url https://app.inso.pl/login \
     --username <email> --password-stdin
   agent-browser auth login inso
   ```
3. Record traffic while clicking through the app:
   ```bash
   agent-browser network har start
   # visit: announcements board, one announcement with photos, messages, documents, child switcher
   agent-browser network har stop /tmp/opencode/inso.har
   ```
4. Analyze HAR: identify JSON endpoints, auth headers/cookies, pagination params, attachment URL
   pattern on file.inso.pl.
5. Re-login test: confirm session persistence (`state save ./auth.json`), note expiry.

**Deliverable:** `docs/api-notes.md` — endpoint table, auth model, payload examples.
**Decision point:** JSON API exists → Python httpx client (default).
API too opaque → fall back to browser-driven scraping (agent-browser in a long-lived session,
`--session-name inso`), same storage layout.

## Phase 1 — Skeleton + auth (M1 start)

- uv-managed project: `uv init`, `pyproject.toml`, package `inso_dumper`, CLI entry
  `inso-dumper` (typer); deps added via `uv add httpx typer rich pydantic`; lockfile
  `uv.lock` committed.
- **All** Python invocations go through uv: `uv run inso-dumper …`, `uv run pytest`,
  `uv run ruff check .`. No bare `python`/`pip`.
- Config loader (TOML + env overrides), session store under `~/.local/state/inso-dumper/`.
- Implement login against discovered flow; verify with a profile/children listing request.

**Check:** `uv run inso-dumper login` prints "OK" and `uv run inso-dumper children` lists real
children.

## Phase 2 — Announcements + photo sets (M1 finish)

- Pydantic models for announcement payloads.
- Fetch full announcement list (paginate), store meta.json per event dir using naming rules (F3).
- Download media ordered, checksum, atomic write, hardlink into `_common/blobs/`.
- Per-event `index.html` gallery.

**Check:** `uv run inso-dumper sync --only announcements --limit 5 --child <slug>` produces correct
tree; re-run is a no-op (state db).

## Phase 3 — Multi-child + dedup (M2)

- Cross-child announcement-id comparison → `_common/announcements/<id>/` + per-child references.
- Blob store + hardlink/symlink layer; collision tests; `verify` command.

**Check:** same photo posted to both children exists exactly once under `_common/blobs/`,
hardlink count ≥ 2; `uv run inso-dumper verify` clean.

## Phase 4 — Messages + documents (M3)

- Conversations JSON + attachments; documents tab files (usually PDFs/jadłospisy).
- Attachments stored under child's `messages/attachments/<conversation-id>/`.

**Check:** message history byte-identical across two consecutive syncs; PDFs open.

## Phase 5 — Incremental + indexes (M4)

- Pagination until seen-id threshold; `--full` re-verify mode.
- `_index.json` + top-level `index.html` (filter by child, year, event-name search).

**Check:** run `sync` twice in a row → second run 0 downloads; new announcement posted in the app
appears after next sync.

## Phase 6 — Polish (M5)

- `materialize` command, `verify` reporting, error taxonomy, README with privacy notes.
- Optional: scheduled weekly run via systemd user timer (docs snippet).

## Risks / mitigations

| Risk | Mitigation |
| --- | --- |
| App is SPA with opaque API or heavy obfuscation | Fall back to browser-driven scraping (Phase 0 decision point) |
| Signed/expiring file URLs | Always re-derive URLs from listings at sync time; never cache bare file URLs |
| Login captcha/2FA | Interactive `login` step once; long-lived session; manual cookie import as last resort |
| Marking messages as read as a side effect | Avoid endpoints with side effects; verify with user before any non-GET |
| Institution switch loses access | Encourage periodic `sync`; dump is local and self-contained |
