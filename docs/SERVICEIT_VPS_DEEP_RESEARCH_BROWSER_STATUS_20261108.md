# Service IT — Deep public research vs real browser on VPS

Status: **optional browser source code STAGED, not installed/activated on VPS**.
This is separate from the four healthy native CRM worker containers.

## What is already in the native production image

- RouterAI DeepSeek model via the permitted synthetic test and current
  private VPS secret file; no GigaChat fallback.
- Public-source title AI route with explicit owner-approved source domains.
- Read-only public HTTPS fetching, robots.txt, URL + public DNS validation.
- Optional Brave Web + LLM Context search, requiring a separately
  approved Brave API subscription key and mounted secret.
- Free GDELT news discovery fallback when Brave is absent; that is not
  comprehensive Internet search.
- Independent company webpage evidence, non-verified candidates,
  manager-facing reports, source quotes, no automatic outreach.
- All findings require independent source validation; unknown legal
  identity, STOP and legal basis remain HOLD.

## What is NOT yet transferred as an active capability

A real headless Chromium/Playwright browser is not present in the
default `Dockerfile.serviceit-vps`. JavaScript-heavy sites cannot be
claimed as fully researched with plain HTTPX page fetching.

This branch supplies an isolated, **disabled** reference implementation:

- `leadpilot/serviceit_vps_public_browser.py`: single-page public
  renderer, owner-defined HTTPS allowlist, DNS/redirect/request/robots
  checks, Chromium sandbox REQUIRED, no login/click/form/download
  actions, no CRM or external AI credentials. CLI prints only sanitized
  status/count/hash, never scraped text.
- `Dockerfile.serviceit-vps-browser`: dedicated, pinned Playwright
  1.64.1 / Ubuntu Noble image, unprivileged `pwuser`.
- `deploy/serviceit-vps-browser.compose.yml`: optional `browser`
  profile, isolated from the production Compose, no published ports,
  no CRM/database/model/GitHub secrets, no startup by default.

## Why it is not automatically switched on

Browser-rendered sites are untrusted. DNS validation before navigation
does **not** guarantee protection against DNS rebinding or browser
sandbox escapes. Chromium requires user-namespace + seccomp support;
a restrictive Docker environment may refuse to launch it. A separate
egress firewall must block private/loopback/link-local/metadata IP
destinations; the same server and database must never be exposed to
browser navigation. Owner must approve specific public domains and
site terms. Both a genuine browser sandbox test and an actual public
site render/readback must pass before it can be connected to the
automatic native public-research worker.

Neither source ingestion, 9 CRM fields, jobs nor finance need a
browser as a prerequisite. Fail closed to truthful HOLD rather than
pretend deep research succeeded.

## Independent live cutover

The native four-worker stack still needs a coordinated CLOUD→VPS
writer cutover and one genuinely new Bitrix category 21 acceptance
card. The browser must not delay the primary new-card/HOLD/Olga605
task automation.
