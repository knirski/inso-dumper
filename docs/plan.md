# Plan: inso-dumper

Companion to [prd](prd.md) and [api-notes](api-notes.md). Ordered, verifiable
steps. Each phase ends with a check.

Status markers: ✅ shipped · 🔄 in progress · ⬜ pending. Deviations from the
original phase intent are noted inline; where a phase grew a spec, the spec is
the source of truth and this file only tracks status.

## Phase 0 — API discovery ✅ (superseded by follow-up spikes)

Original intent: agent-browser HAR capture to decide between a JSON API client
and HTML scraping. What was learned (full detail in `api-notes.md`):

- Auth: one-step form POST, `PHPSESSID` HttpOnly cookie, no CSRF.
- **Hybrid reality**: children/scraping is HTML, but the content surfaces are
  JSON `system-api` endpoints (`/system-api/timeline/posts`,
  `/system-api/conversations`, `/drive/items`) — both original branches were
  half right.
- Cloudflare passes `httpx` with a browser-like header set (pinned as
  `BROWSER_LIKE_HEADERS`).

Decision point resolved: Python `httpx` client; HTML parsing only for the
children list. Follow-up spikes (also Aug 2026) captured the timeline,
conversations, and drive endpoints; captures live in the gitignored
`docs/api-notes-data/`.

**Deliverable:** `docs/api-notes.md` ✅ (kept current as spikes land).

## Phase 1 — Skeleton + auth ✅

Shipped per `docs/specs/2026-08-29-foundation-auth-cli/design.md`: uv project,
typer CLI, config loader, session store, `login`/`children` commands, exit-code
taxonomy, `HttpxClient` transport.

**Check:** met — `login` succeeds, `children` lists real children.

## Phase 2 — Announcements + photo sets ✅

Shipped per `docs/specs/2026-08-29-announcements-and-dedup/design.md` (tasks
T1–T9, PRs #3/#4). Deviations from the original sketch:

- JSON timeline API, not HTML parsing; page size 10, `waitingToProcess`
  backoff.
- Per-post files are `post.json`/`post.html`/`post.md` (not
  `meta.json`/`index.html`); gallery `index.html` is deferred to Phase 5.
- Incremental: full list walk each run + manifest-keyed skip + `--force
  <slug>` per-post override (no `--full`/`--limit` flags).

**Check:** met — first real sync of both children's announcements and
galleries; markup drift (trailing-slash 301, missing `el-menu` id) and the
video shape were found and fixed loud, not silently (#5, #6).

## Phase 3 — Multi-child + dedup ✅

Shipped with Phase 2 (same spec): content-addressed blob store at
`_common/{photos,videos,attachments}/`, hardlinks with symlink fallback and
plain-copy fallback, per-child `.manifest.sqlite` state.

Deviation: announcement-level dedup (PRD F4.2) resolved as **media-level**
dedup — the same post dumped to two children gets two post dirs but the blob
bytes exist once. There is no `_common/announcements/` area. `verify` is
deferred to Phase 6.

**Check:** met — re-runs skip recorded posts; media bytes stored once.

## Phase 4 — Messages + documents 🔄

Split into two specs:

- **Messages** — `docs/specs/2026-08-29-messages-and-documents/plan.json`
  approved (10 tasks, PR #8 merged). Key discovery: conversations are
  **account-level** (`?child=` accepted but ignored), so storage is
  `dump/messages/`, amending the PRD sketch. Manifest schema v3 adds a
  `conversations` table; tail fetch via `startingIndex`.
- **Documents** — same spec, but gated: only `breadcrumbs` of the drive API
  could be verified (empty drive). `sync_documents` fails safe
  (`documents_unverified`) until a real capture pins the file/download shapes.
- **Settlements ("Rozliczenia")** — split out entirely: server-rendered HTML +
  invoice PDFs, not covered by the JSON API. Own follow-up spec (Phase 7).

**Check (messages):** message history byte-identical across two consecutive
syncs; attachments open.
**Check (documents):** blocked until the drive download URL is captured from
the user's real session.

## Phase 5 — Incremental + indexes 🔄

- ✅ Incremental for posts (manifest skip, `--force`), incremental for
  conversations planned in the messages spec (manifest v3 tail fetch).
- ⬜ `_index.json` + top-level `index.html` (by date/child/event name).
- ⬜ Per-event `index.html` gallery (deferred from Phase 2).

**Check:** two consecutive syncs → second run 0 downloads (already true for
posts); a new announcement/message appears after the next sync.

## Phase 6 — Polish ⬜

- ⬜ `verify` command (checksum recompute, corrupt-blob report).
- ⬜ `materialize` command (resolve hardlinks/symlinks to copies).
- ⬜ README with privacy notes; packaging (`uv tool install`).

## Phase 7 — Settlements follow-up spec ⬜

Read-only dump of "Rozliczenia" (`/panel/billing/<child-uuid>/`):
server-rendered HTML scrape + invoice PDF downloads. Needs its own spike +
spec; PRD non-goals amended to permit read-only billing access (no writes,
ever).

## Risks / mitigations

| Risk | Status | Mitigation |
| --- | --- | --- |
| Opaque API / heavy obfuscation | **resolved** — hybrid HTML + JSON `system-api` | JSON endpoints documented in `api-notes.md` |
| Signed/expiring file URLs | **resolved** — CloudFront signing confirmed (~hours) | Download immediately after listing; never persist URLs |
| Cloudflare bot management | **resolved** — browser-like header set passes | `BROWSER_LIKE_HEADERS` pinned, test-asserted; transport swap possible via `HttpClient` Protocol |
| Login captcha/2FA | none encountered | Re-open only if an account triggers it |
| Marking content read as a side effect | **mitigated** | Read-only guarantee: `PUT /view`, `read-all-unread`, uploads, and drive writes are never called (test-pinned) |
| Markup drift (server-rendered parts) | **hit once** — children switcher, 301s (#5) | Parsers fail loud (`PLATFORM_CHANGED`); fix from fresh capture |
| Institution switch loses access | open | Encourage periodic `sync`; dump is local and self-contained |
| Manifest schema changes | **hit once** — v3 bump planned | Loud gate (`user_version not in (…)` → error, no silent migration); recovery documented in the spec |
