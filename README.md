# inso-dumper

Local backup tool for the Inso platform ([app.inso.pl](https://app.inso.pl)).
Authenticates against a parent account and dumps announcements, messages, and
documents available to that parent, with deduplication into a shared area.

This ships the **foundation** (M1), **announcements-and-dedup** (M1 finish +
M2), **messages-and-documents** (M3: communicator + drive), **settlements**
(M6), and **indexes + integrity audit** (Phase 5/6) specs: `inso-dumper
login`, `children`, `sync` (announcements, galleries, messages, settlements),
`index`, `verify`, and `materialize`. Documents remain gated on a real
drive capture (see below).

## Quick start

```bash
# one-time: install dependencies (requires uv and Python 3.14)
uv sync

# authenticate (PHPSESSID persisted under ~/.local/state/inso-dumper/)
export INSO_EMAIL=you@example.com
export INSO_PASSWORD=...
uv run inso-dumper login

# list children on the account
uv run inso-dumper children

# JSON for scripting
uv run inso-dumper children --json

# dump everything (announcements + galleries per child, messages +
# documents per account), deduplicated and incremental
uv run inso-dumper sync <child-slug>

# one category only
uv run inso-dumper sync <child-slug> --category messages
```

Announcements and galleries are per child; messages and documents are
account-level (the platform scopes them to the parent account, not the
child) and are synced once per run regardless of the child argument.

## Sync

`inso-dumper sync <child-slug>` walks the platform APIs, downloads all
media, and writes the dump under `./dump/` (override with
`--dump-root PATH`):

```
dump/
├── _common/                 # shared media store, deduplicated by content hash
│   ├── photos/<hash[:2]>/<hash>.<ext>
│   ├── videos/<hash[:2]>/<hash>.<ext>
│   └── attachments/<hash[:2]>/<hash>.<ext>
├── <child-slug>/announcements/
│   ├── .manifest.sqlite     # per-child posts sync state
│   └── <YYYY-MM-DD>-<post-slug>/
│       ├── post.json / post.html / post.md
│       ├── photos/1.jpeg    # symlink into _common/
│       └── videos/…/files/  # only if present
├── messages/                # account-level communicator
│   ├── .manifest.sqlite     # conversations state (incremental tail fetches)
│   └── <YYYY-MM-DD>-<group-slug>/
│       ├── conversation.json
│       ├── messages.json    # full history, appended incrementally
│       └── attachments/<message-id>/1.jpeg → _common/
├── <child-slug>/settlements/<YYYY-MM>/
│   ├── settlement.json      # parsed month card (items, saldo, status)
│   └── invoice.pdf          # when the month has a downloadable invoice
├── documents/               # account-level drive (see gate note below)
├── index.html               # top-level index (offline, rebuilt by `index`)
└── _index.json              # machine-readable index of the whole dump
```

Re-runs skip already-dumped posts and unchanged conversations (by
`last_update`); changed conversations fetch only new messages.
`--force` (repeatable) re-dumps a named item — post slug for
announcements/galleries, conversation id or directory name for messages
(full history re-fetch from 0), YYYY-MM for settlements. A
`messages.json` that disagrees with the manifest is never appended to;
the recovery is `--force`.

`inso-dumper index` (offline, no session) rebuilds `_index.json`, a
top-level `index.html`, and a per-event gallery `index.html` from the
dump tree — so "find photos from Dzień Kolorowy 2025" is a click away.

`inso-dumper verify` (offline) re-hashes every `_common/` blob against
its content-addressed filename and resolves every symlink; it exits 0
when the dump is clean and 1 with a findings report (corrupt blobs,
dangling links). `inso-dumper materialize` (offline) replaces symlinks
with real copies so the per-child tree is self-contained; `_common/`
is kept — future syncs still dedup against it. Both are safe to re-run.

**Documents gate:** the drive file-download shape could not be verified
during discovery (empty drive), so the documents category is a loud
skip until a non-empty drive capture pins it — it never guesses.

The dumper is read-only against the platform: GET requests only, signed
media URLs are never persisted, and mark-as-read/upload endpoints are
never called.

## Privacy

The dump contains children's personal data (names, photos, messages,
billing). Treat the whole tree as sensitive:

- Directories are created `0700` and files `0600`; never commit `dump/`
  or anything derived from it.
- The `PHPSESSID` session file grants full account access — it lives
  outside the repo (`~/.local/state/inso-dumper/`) and never enters
  logs (tokens are redacted).
- If you share excerpts of a dump (bug reports, screenshots), redact
  names, faces, and message text first.

## Environment variables

| Variable | Required by | Notes |
| --- | --- | --- |
| `INSO_EMAIL` | `login` | Parent account e-mail. |
| `INSO_PASSWORD` | `login` | Parent account password. |
| `INSO_DUMPER_CONFIG` | both | Alternate TOML config file path. |
| `INSO_DUMPER_VERBOSE` | both | `1` for DEBUG-level logging. |

## Configuration file

TOML at `~/.config/inso-dumper/config.toml`:

```toml
base_url = "https://app.inso.pl"
rate_limit_per_second = 3.0
request_timeout_seconds = 30.0
```

## Session file

`PHPSESSID` and the post-login user UUID are persisted at
`~/.local/state/inso-dumper/session.json` with mode `0600`. The file is
re-validated on every `children`/`sync` run; no refresh path — re-login
on expiry.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 1 | `verify` found integrity issues (corrupt blobs / dangling links) |
| 2 | Configuration / env error |
| 3 | Authentication failed |
| 4 | Saved session expired or missing — run `inso-dumper login` |
| 5 | HTTP transport failure |
| 6 | Platform payload shape changed |
| 99 | Unclassified internal error |

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -q
```

See `AGENTS.md` for the project's Python style, secrets policy, and the
functional-core / imperative-shell boundary.
