# inso-dumper

Local backup tool for the Inso platform ([app.inso.pl](https://app.inso.pl)).
Authenticates against a parent account and dumps announcements, messages, and
documents available to that parent, per child, with deduplication into a
shared area.

This is the **foundation** spec (M1) plus the **announcements-and-dedup**
spec (M1 finish + M2). It ships `inso-dumper login`, `inso-dumper
children`, and `inso-dumper sync` (announcements and galleries with
photo/video/attachment deduplication); messages, documents, indexes, and
the `verify`/`materialize` sub-commands land in follow-up specs.

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

# dump announcements + galleries for one child (deduplicated)
uv run inso-dumper sync <child-slug>

# one category only
uv run inso-dumper sync <child-slug> --category announcements
```

## Sync

`inso-dumper sync <child-slug>` walks the timeline API per category,
downloads every post's media, and writes the dump under `./dump/`
(override with `--dump-root PATH`):

```
dump/
├── _common/                 # shared media store, deduplicated by content hash
│   ├── photos/<hash[:2]>/<hash>.<ext>
│   ├── videos/<hash[:2]>/<hash>.<ext>
│   └── attachments/<hash[:2]>/<hash>.<ext>
└── <child-slug>/announcements/
    ├── .manifest.sqlite     # sync state (idempotent re-runs)
    └── <YYYY-MM-DD>-<post-slug>/
        ├── post.json / post.html / post.md
        ├── photos/1.jpeg    # symlink into _common/
        ├── videos/…/files/  # only if present
```

Re-runs skip already-dumped posts (nothing is re-downloaded).
`--force <post-slug>` (repeatable) re-downloads a named post's media.
The dumper is read-only against the platform: GET requests only, signed
media URLs are never persisted, and mark-as-read is never called.

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
