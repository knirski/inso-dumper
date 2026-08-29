# inso-dumper

Local backup tool for the Inso platform ([app.inso.pl](https://app.inso.pl)).
Authenticates against a parent account and dumps announcements, messages, and
documents available to that parent, per child, with deduplication into a
shared area.

This is the **foundation** spec (M1). It ships `inso-dumper login` and
`inso-dumper children`; content sync, deduplication, indexes, and the
`verify`/`materialize` sub-commands land in follow-up specs.

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
```

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
re-validated on every `children` run; no refresh path — re-login on expiry.

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
