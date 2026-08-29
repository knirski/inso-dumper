# Inso API notes

Spike output from `plan.md` Phase 0. Captured 2026-08-29 against
https://app.inso.pl. This document is the **required input** for
`docs/specs/2026-08-29-foundation-auth-cli/design.md`; the spec's
assumptions are replaced by what is recorded here.

## TL;DR

The Inso platform is a **server-rendered PHP application** behind
Cloudflare. There is **no JSON API** for parent-facing content. Auth
is a one-step form POST that returns a single `HttpOnly PHPSESSID`
cookie. The children list is **embedded in the HTML of any logged-in
page** in the sidebar's child-switcher menu. The HTML-scrape path
in `plan.md` Phase 0 is the correct one.

Cloudflare blocks bare HTTP clients, but **passes `httpx` with a
realistic browser header set** — confirmed in the spike and a
follow-up probe on 2026-08-29. See *Cloudflare bot management* below.
The `HttpxClient` production transport sends a fixed
`BROWSER_LIKE_HEADERS` constant on every request.

## Auth model

| Concern | Value | Source |
| --- | --- | --- |
| Login URL | `https://app.inso.pl/login` (GET and POST) | HAR entry 41 |
| Method | `POST` form-encoded | HAR entry 41 |
| Content-Type | `application/x-www-form-urlencoded` | HAR entry 41 |
| Username field | `_username` | HAR entry 41 |
| Password field | `_password` | HAR entry 41 |
| CSRF token | **none** | confirmed by inspecting `login.<hash>.js` and the POST request |
| Required headers | `Origin`, `Referer`, `Upgrade-Insecure-Requests`, normal UA | HAR entry 41 |
| Response | `302 Found` with `Location: /panel/` | HAR entry 41 |
| Set-Cookie | **`PHPSESSID=<opaque>; path=/; secure; httponly; samesite=lax`** | HAR capture via `curl` |
| Remember-me | `REMEMBERME=deleted` cleared on every login (server-side "you don't have one") | HAR capture via `curl` |
| Post-login redirect chain | `/login` → `/panel/` → `/panel/home/<user-uuid>/` | HAR entries 41–43 |
| Session lifetime | not measured; `PHPSESSID` is the standard PHP session cookie (server-side expiry) | inferred |
| Refresh mechanism | none — re-login on expiry | n/a (v1) |
| 2FA | none encountered | spike did not trigger any second factor |

### What the client must do

1. GET `/login` once to satisfy any pre-session cookies and CSRF (none
   required, but cheap and defensive).
2. POST `/login` with `_username` and `_password` as
   `application/x-www-form-urlencoded`. Follow redirects.
3. Capture `PHPSESSID` from `Set-Cookie`. Persist it. Send it on every
   subsequent request.

### What the client must NOT do

- Send any custom auth header. There is no `Authorization: Bearer …`
  and no `X-…-Token` to forge.
- Assume the body of a successful login response contains the user.
  The body is empty; the user UUID is the path segment of the final
  `Location`.

## Cloudflare bot management

A bare `httpx` request (no headers set beyond defaults) is **rejected
with HTTP 403** by Cloudflare's bot management. A bare `urllib`
request is rejected for the same reason. `curl` succeeds.

**Re-tested on 2026-08-29 with browser-like headers** (User-Agent,
sec-ch-ua, Accept-Language: pl-PL, sec-ch-ua-platform, etc. — the
exact set is documented in
`docs/specs/2026-08-29-foundation-auth-cli/design.md` § Cloudflare
verification). With those headers, `httpx` gets HTTP 200 and a clean
14 KB HTML response. End-to-end login + authenticated dashboard
fetch also works through `httpx` (302 on POST `/login` with
`Set-Cookie: PHPSESSID=…`, then GET dashboard returns 200 with
both children's names visible).

**Conclusion for v1:** `HttpxClient` is the production transport. No
`curl_cffi`, no subprocess `curl`, no `agent-browser` fallback. The
`HttpxClient` pins a `BROWSER_LIKE_HEADERS` constant and includes it
on every request; a test asserts the constant stays present.

If Cloudflare ever tightens to the point of rejecting this header
set, the existing `HttpClient` Protocol accommodates a transport
swap (subprocess `curl`, `curl_cffi`, or `agent-browser`) without
call-site changes. The exit code 5 (`HTTP`) covers the failure
mode regardless.

## Children endpoint

**There is no `GET /api/children`.** The children list is rendered
into the HTML of any logged-in page. The spike saw it in:

- `https://app.inso.pl/panel/home/<child-uuid>/` (current child's
  dashboard)
- Every other `/panel/...` and `/timeline/...` page, in the sidebar
  child-switcher menu (`<el-menu id="menu-0">`).

### URL pattern for a child's dashboard

```
https://app.inso.pl/panel/home/<child-uuid>/
```

`<child-uuid>` is a v4-style UUID, e.g.
`eea48660-3740-11ed-a611-06dd2728d782` (Ignacy) and
`a703dc5a-042d-4122-96e8-4c5111e5f7cf` (Liliana). The UUID is stable
across sessions and is the canonical child identifier.

### Sidebar markup that exposes children

```html
<el-menu id="menu-0" role="menu">
  <a href="https://app.inso.pl/panel/home/<uuid-1>" id="item-2" role="menuitem">
    <div style="background-color: #FCCC34;"><span>IG</span></div>
    <span>Ignacy</span>
  </a>
  <a href="https://app.inso.pl/panel/home/<uuid-2>" id="item-3" role="menuitem">
    <div style="background-color: #A9CC63;"><span>LI</span></div>
    <span>Liliana</span>
  </a>
  <a href="/panel/add-child" id="item-4" role="menuitem">
    <div><i class="fal fa-plus"></i></div>
    <span>Dodaj dziecko</span>
  </a>
</el-menu>
```

### `Child` model (revised by the spike)

The spec's draft `Child` model was missing a few fields that the
spike found in the markup. Revised fields:

| Field | Source | Type |
| --- | --- | --- |
| `child_id` | UUID segment of `/panel/home/<uuid>/` | `str` |
| `first_name` | `<span>` inside the `<a>` menu item | `str` |
| `last_name` | not present in the spike's two children; appears in the URL slugs of `/messages?child=<uuid>` and `/timeline/child/<uuid>` page titles. The spike's home page title was "Podsumowanie - Nirski Ignacy - Inso" — full name is available on the dashboard. | `str` |
| `group` | the home page title does not include a group; sidebar items don't either. The group name is likely on a per-child profile page that the spike did not visit. | `str \| None` (kept from spec) |
| `avatar_color` | the `<div style="background-color: #XXXXXX;">` in each menu item | `str` (CSS hex) |
| `initials` | `<span>` inside the avatar div, e.g. `IG`, `LI` | `str` |

The `last_name` is reachable through `/messages?child=<uuid>` (page
title contains the full name), but **fetching every child just to
read the last name is wasteful**. The spec's `Child.slug` rule
(`firstname-lastinitial` or similar) is fine without the last name;
the foundation spec should add a documented `last_name` discovery
field that future specs may fill in lazily, not on the children
list call.

### `list_children` derivation

`list_children()` is **not a network call** in v1. It is:

1. Load the saved `Session` (`PHPSESSID`).
2. GET `https://app.inso.pl/panel/home/<any-uuid>/` (or the
   post-login landing page; either contains the sidebar). Any
   logged-in page works.
3. Parse the `<el-menu id="menu-0">` block; extract each `<a>`
   whose `href` matches `^https://app.inso.pl/panel/home/<uuid>/?$`.
4. Return `list[Child]`.

The "any logged-in page" choice is the cheapest. The post-login
landing `/panel/home/<user-uuid>/` is the natural target — it is
always 200, always has the sidebar, and its `<child-uuid>` happens
to be the last-active child.

## Other endpoints discovered (out of scope for v1)

These URLs appeared in the sidebar's navigation links and are
recorded here so the follow-up specs (`announcements-and-dedup`,
`messages-and-documents`) can target them without re-running the
spike. The spike did not capture their bodies; that is the
respective follow-up spec's job.

| Section | URL pattern | What it shows |
| --- | --- | --- |
| Dashboard (Podsumowanie) | `/panel/home/<child-uuid>/` | Today's summary; sidebar |
| Messages (Wiadomości) | `/messages?child=<child-uuid>` | Conversations list |
| Announcements (Ogłoszenia) | `/timeline/child/<child-uuid>` | Announcements feed |
| Billing (Rozliczenia) | `/panel/billing/<child-uuid>/` | Fees — **out of scope per PRD Non-goals** |
| Attendance calendar | `/panel/attendance/<child-uuid>/` | Calendar |
| Events | `/events/child/<child-uuid>` | Events list |
| Files (Pliki) | `/panel/files/<child-uuid>/` | Documents / file library |
| Diaries (Dzienniki) | `/diaries/child/<child-uuid>` | Daily reports |
| Child data | `/panel/child-data/<child-uuid>/` | Profile, parents, pickup |
| Authorizations | `/panel/authorizations/<child-uuid>/` | Pickup authorizations |
| Add child | `/panel/add-child` | New child wizard (skip) |

All of these are also server-rendered HTML, with the same sidebar.
The spike did not check for any JSON endpoint behind them; the
follow-up spike for `announcements-and-dedup` should.

## Session model (spec revision)

The foundation spec's draft `Session` model was over-specified. The
spike shows the **minimum persisted state** is:

```python
class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = 1
    phpsessid: str                  # the value of PHPSESSID
    user_uuid: str                  # the post-login landing UUID
    expires_at: datetime | None     # unknown; PHP sessions are server-side.
                                     # Conservative: None, treat as
                                     # always-needs-validation, with
                                     # a future spike measuring real expiry.
```

The `bearer_token`, `refresh_token`, `csrf_token`, and `raw_metadata`
fields in the spec's draft are **not needed** for v1. Drop them. The
`cookies: dict[str, str]` field can be reduced to `phpsessid: str`
plus `remember_me: str | None` for forward compat, but v1 only needs
`phpsessid`.

## Open Questions (still open after this spike)

1. **Session lifetime** — not measured. The `expires_at` field in
   the persisted session is currently always `None` and treated as
   "validate on every load" (i.e. re-login required every run).
   The follow-up announcement spec can measure the real value.
2. **2FA on this account** — none encountered. The spike logged in
   cleanly. If a future account has 2FA, the foundation is
   unprepared: the `login` command's error taxonomy has no
   `NEEDS_2FA` variant. **Defer to that account.**

## How to reproduce this spike

```bash
# 1. Save credentials once (interactive, password on stdin):
agent-browser auth save inso \
  --url https://app.inso.pl/login \
  --username <email> --password-stdin

# 2. Drive a headful session, capturing everything:
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser open https://app.inso.pl/login
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser auth login inso

# 3. With HAR running, navigate to the children switcher:
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser network har start
AGENT_BROWSER_EXECUTABLE_PATH=/path/to/chrome \
  agent-browser network har stop docs/api-notes/har/spike-<date>.har

# 4. For the cookies themselves, use curl (HAR cannot see
#    HttpOnly Set-Cookie on a 3xx chain):
curl -c jar -b jar -L --data-urlencode '_username=…' \
  --data-urlencode '_password=…' -X POST \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Referer: https://app.inso.pl/login' \
  https://app.inso.pl/login
# inspect jar for PHPSESSID
```

`docs/api-notes/har/spike-2026-08-29-login.har` (55 requests) and
`docs/api-notes/har/spike-2026-08-29.har` (12 requests, mid-session)
are the artifacts.
