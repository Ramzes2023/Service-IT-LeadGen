# Brave Search VPS HTTP 422 incident (2026-11-08)

## Observed production-safe statuses

Run [42521838093](https://github.com/Ramzes2023/Service-IT-LeadGen/actions/runs/42521838093)
blocked before coordinator restart with
`BLOCKED_PHASE=BRAVE_AUTH_GENERIC_WEB_AND_CONTEXT` and
`BRAVE_ROLLBACK=NEEDS_OPERATOR_REVIEW`.

Read-only follow-up run
[42522554951](https://github.com/Ramzes2023/Service-IT-LeadGen/actions/runs/42522554951)
confirmed `NATIVE_FOUR_HEALTHY=PASS`, `CHROMIUM_HEALTHY=PASS`,
`BRAVE_SECRET_MOUNT_IN_LIVE=NO`, `BRAVE_WEB_STATUS=BRAVE_PLAN_OR_QUERY_REJECTED_422`,
and `BRAVE_LLM_CONTEXT_STATUS=NOT_CALLED`.
Thus Brave is **not** yet enabled in the existing native coordinator.
No LIVE container was restarted by that read-only diagnostic.

Official Brave API documents an optional but strict `Cache-Control: no-cache`
request header. A 2026 upstream issue reported an HTTP 422 `VALIDATION`
error on `header/cache-control` when callers sent an incompatible value.
This is a candidate cause, **not yet confirmed for our VPS request**.
References:
- https://api-dashboard.search.brave.com/api-reference/web/search/get
- https://github.com/openclaw/openclaw/issues/2476

## Narrow fix

- Every project Brave API request now explicitly sends
  `Cache-Control: no-cache`, including Web Search, LLM Context and probe.
- HTTP 422 may be safely classified by whitelisted `error.meta.errors[].loc`
  values only: `header/cache-control`, `query/country`, or
  `query/search_lang`; otherwise report generic 422. Never print
  error messages, request URLs, headers, private company names or tokens.
- Existing guarded owner-only activation prints only whitelisted Web/Context
  status codes when the disposable smoke fails. On pre-cutover error, it
  checks the existing coordinator's unchanged image ID and the four LIVE
  services and browser. A failure before coordinator cutover does not imply
  a LIVE restart; image-tag restore problems are not silently ignored.
- Authentication smoke remains **one Web plus one Context request maximum**
  with no retry on failed Web request. No CRM writes, source connections,
  new cards, AI inference, private records, or key transfers to GitHub.
- No change to existing three-search budget per eligible card (two Web and
  one Context), nor to allowlists and Chromium sandbox.

## Operator next step

After merge/CI, owner may repeat the *existing* guarded manual workflow
`serviceit-vps-brave-live-activate.yml` with
`approval=ACTIVATE_VPS_BRAVE_SEARCH_CONTEXT_1062_21`.
It refuses a new LIVE coordinator restart if either real API fails.
If HTTP 422 persists, inspect its **safe** `BRAVE_WEB_STATUS` and verify
subscription status via the official Brave dashboard instead of changing
headers blindly. Do not use unofficial proxies to bypass provider regional
restrictions. Real new-card Bitrix24 9-field processing is not yet E2E verified.
