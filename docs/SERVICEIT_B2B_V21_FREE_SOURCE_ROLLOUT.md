# V21 free B2B source fallback: rollout contract — 11.11.2026

## Scope actually implemented
- Existing V11/V19/V20 native research calls read_free_public_mirror()
  only after ordinary HTTP, Brave, browser and mirror research all fail.
- The helper first tries already-indexed unverified Brave URL candidates;
  otherwise at most one TinyFish Search and one TinyFish Fetch URL.
- Index snippets never become facts; exact full-page tender ID/title checks
  must pass. The resulting evidence goes into current RouterAI/citations.
- No additional polling or CRM writer, new lead, new PG table, STOP/HOLD,
  contacts, tasks, scoring, GigaChat or uncontrolled replay.
- Existing one-use PostgreSQL receipts govern re-research cadence.

Official provider pricing: Search free (500 requests/hour), Fetch free
(1000 URLs/day): https://www.tinyfish.ai/pricing
Paid Agent, Browser and Monitor are not invoked by this fallback.

## Prerequisites — OFF until every gate passes
1. Review privacy and source-specific terms for provider extraction.
2. Obtain independent free API key: https://agent.tinyfish.ai/api-keys.
   NEVER put its value in ChatGPT, GitHub, Bitrix or logs.
3. The VPS administrator must store this key as a mounted secret.
   Inside the coordinator the only allowed configuration is
   SERVICEIT_TINYFISH_API_KEY_FILE=/run/secrets/...
   (a real readable secret file, NOT a plaintext env value).
   ChatGPT's TinyFish app authorization is NOT the VPS API key.
4. After exact code hash verification and owner approval:
   SERVICEIT_VPS_B2B_FREE_PUBLIC_FETCH_ENABLED=YES
5. Source rights are independent:
   SERVICEIT_B2B_TENDEM_PUBLIC_EXTRACTION_APPROVED=YES after approval;
   SERVICEIT_B2B_CENTER_PUBLIC_EXTRACTION_LICENSED=YES only after
   documented permission for unattended B2B platform extraction.
   Ordinary site visibility does not equal automation permission.
   Kontur, Synapse access controls, CAPTCHA and logins are NOT bypassed.
6. Existing RouterAI privacy and V11/V19/V20 safety flags must stay ON.

## Read-only VPS readiness audit
The repository includes owner-only GitHub workflow
.github/workflows/serviceit-vps-b2b-free-source-readonly.yml.
Owner can trigger it by posting an exact comment to Issue #368:

    SERVICEIT_B2B_FREE_SOURCE_READONLY_368

It checks the four Python file hashes against current main, whether the
secret is mounted (file presence and size only), and current approval flags.
It does NOT read key bytes, query Postgres, call RouterAI, fetch websites,
change Docker, or write any Bitrix CRM data. A result of
FREE_SOURCE_CODE=NOT_INSTALLED is normal BEFORE deployment.

## Guarded production rollout is a separate action
Use a SHA-pinned immutable offline Docker image and the currently live
Compose pins; preserve an exact old-image rollback. Never hot-copy code
into a live container, restart adjacent services, or start a second writer.
After deploying OFF-mode, re-run the audit; require code hash/secret/flags
match and independent coordinator health check.

Only then allow one source and one URL per eligible evidence-only research
attempt. V19/V20 POST_UNCERTAIN and ambiguous Bitrix outcomes must remain
frozen, not automatically retried. Inspect existing 4 cards read-only.
Any real CRM write must undergo exact 9-field and non-AI before/after
readback, with source links and immutable receipt. No facts are invented
when pages remain inaccessible. The code PR and this audit alone do NOT
mean VPS installation or live card enrichment succeeded.
