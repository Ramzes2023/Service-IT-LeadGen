# Brave LLM Context inside the existing ServiceIT 3-call search budget

**Scope:** Only the existing source-neutral new-card Bitrix24 1062/21
preliminary public-web enrichment. Does not ingest sources, update historical
cards, create tasks/contacts, alter corporate STOP or after-creation duplicate
checks, adjust formal Lead Score or authorize contacting employers.

## Search budget per eligible new card

- **Before:** 3 Brave Web Search calls.
- **Now:** 2 Brave Web Search calls (official site/contacts + projects/news/
  procurement) and **1 Brave LLM Context call** (branch expansion, IT/1C
  projects, employment, digitalization).
- **Hard application budget:** at most 3 Brave search requests per new card.
  If LLM Context fails, KEEP the 2 Web calls; NEVER make a fourth paid retry.
- If no name has been published by the original notice, send **zero**
  external index queries. If the API key is unavailable, fall back to
  existing free GDELT news discovery as before.
- Approximate Search-plan pricing verified November 2026: **$5/1000
  Search requests, $5 monthly free credits** for both Web and LLM Context.
  Thus 300 eligible cards with 3 Brave requests each = 900 calls, $4.50 of
  credits before other uses. The theoretical upper bound is 333 cards,
  excluding smoke tests, errors, manual use and other projects.
  Main RouterAI/GigaChat inference fees are separate. Do not assume that
  the provider prevents overage automatically; configure usage restrictions.

## Evidence flow

1. Source-linked publicly readable job/tender/service page is checked with
   HTTPS, domain allowlist, robots.txt, DNS filtering and read size cap.
2. Only employer/company names published there are sent to Brave.
3. LLM Context returns **pre-extracted snippets**. It is used **only to
   find candidate URLs**, not proof of company ownership, contracts, need,
   timing or published contact. All returned snippets are untrusted, and
   none are directly injected into the model or CRM.
4. At most six independent pages / nine attempts / five public hosts are
   fetched separately, without redirects and after robots/public DNS checks.
   The company name must appear on the fetched page before the result is
   included. Company namesakes still require human confirmation.
5. Only the separately fetched, contact-free short public excerpts
   (up to three pages) are allowed to enter approved AI. Original URLs,
   conditional company-expansion/IT service hypotheses and published
   business-contact candidate URLs go into the SAME nine AI fields.
6. Signed new-card event, encrypted one-use receipt, exact Bitrix scope,
   ownership privacy approval, before-image fingerprint, write fencing,
   exact nine-field readback and no-blind-retry gates remain unchanged.

## API / deployment

Brave endpoint: `GET https://api.search.brave.com/res/v1/llm/context`
with the **existing** secret `SERVICEIT_BRAVE_SEARCH_API_KEY`.

New workflow env is `SERVICEIT_BRAVE_LLM_CONTEXT_ENABLED=YES` in:
- `.github/workflows/serviceit-bitrix-new-card-ai.yml`;
- `.github/workflows/serviceit-bitrix-all-new-batch-ai.yml`.

The read-only readiness workflow now tests exactly **one real LLM Context
request** (rather than a Web Search request), with a generic public Python
documentation query, and reads only corporate CRM AI field metadata.
No customer data is searched; no model calls, real card updates or contacts
are sent in this smoke test. No new secret needs to be configured.

Official docs: https://api-dashboard.search.brave.com/documentation/services/llm-context
Official Search plan: https://api-dashboard.search.brave.com/app/plans

**Live card proof remains pending** until a genuine eligible new card exists
and its complete evidence -> model -> exact Bitrix readback is observed.
