# ServiceIT: independent company intelligence for new LeadGen AI cards

Status: **code integration / offline CI; genuine new-card CRM acceptance still pending.**

The enhancement runs only as enrichment of **genuine new** Bitrix24 smart-process
1062 / category 21 cards through the already gated signed-event and batch
workers. It DOES NOT connect new lead sources, create any lead, create a
client/contact/deal, change the manager task, update old cards, alter the
existing post-creation duplicate check, replace the own LeadGen STOP-list,
authorize cold outreach, or calculate formal Lead Score.

## Research plan on each new card

1. Recheck the original public notice and source-linked pages.
2. Determine an employer/company name **published on the notice itself**
   (structured JobPosting hiringOrganization or an exact matching name in
   visible notice text). An uncorroborated CRM-only company name is never
   sent to a public search engine.
3. Independently research company news via the free GDELT DOC API when
   no paid/credentialed index is configured (one request per company card,
   bounded to recent public news, no separate API key).
   With an optional licensed Brave Search API key, run up to THREE broader
   independent web queries per card:
   - company official website / public business contacts;
   - expansion/new branches/stores or IT systems, depending on signal type;
   - public projects, procurement, job openings and news.
4. Search results are CANDIDATES, never facts. Independently fetch at most
   SIX extra publicly accessible pages from at most FIVE hosts; reject IP
   literals, private DNS, redirects, robots exclusions and overlarge content.
   Record each actually fetched URL. Require the source-published company
   name to occur in the fetched page body before storing a research claim.
   Namesakes and group affiliates remain unverified.
5. For possible opening, expansion, IT modernization, network equipment,
   workforce hiring and tenders, generate **explicit conditional hypotheses**
   (which of 1C, IT outsourcing, Bitrix24, KKT, EDI, networks, equipment may
   matter and precisely what the manager must verify).
6. Extract publicly displayed role-based business email and business phone
   from possible company domains, never from an index snippet. Attach the
   exact URL/date; mark the website itself as a CANDIDATE, not an
   independently confirmed company. Never invent a person's identity.
7. Append source citations, contradictions, unknowns and a conservative
   next-action recommendation to the SAME nine existing Bitrix AI fields.
   The model sees only a bounded contact-free public excerpt under the
   existing corporate AI privacy gate; it NEVER creates contact details or
   a verified buyer intent. All outreach remains MANAGER-ONLY after STOP,
   CRM deduplication and legal checks.

## Important dependency: independent search index

The independent web-search stage uses the **official Brave Search API**,
not unauthorized scraping of Bing / Google / job boards.

Configure GitHub Actions secret SERVICEIT_BRAVE_SEARCH_API_KEY and verify
the Brave API plan independently in the user's own account. Both existing
new-card workflows set SERVICEIT_DEEP_COMPANY_SEARCH_ENABLED=YES. When the key
is missing, the system does **not** call a full-web search API; instead it
attempts a separate **free GDELT news index request**, and records
FREE_NEWS_SEARCH_DONE_FULL_WEB_KEY_MISSING (results found),
FREE_NEWS_NO_MATCH_FULL_WEB_KEY_MISSING (no news found), or
FREE_NEWS_UNAVAILABLE_FULL_WEB_KEY_MISSING (unavailable/rate-limited).
These are deliberately NOT full-web results. Source-linked website research
continues to work with or without either index.

As of November 2026, the published Brave Search plan advertises **$5 free
monthly credits**, with Search API $5 / 1000 queries and billing requiring
a payment card for account creation. Three queries per 300 cards equals 900
index calls / approximately $4.50 **if all cards contain usable public company
names**; retries, other uses, paid usage and further usage are outside this
estimate. No charges are initiated by these code changes.

Free news endpoint: https://api.gdeltproject.org/api/v2/doc/doc
GDELT attribution: https://www.gdeltproject.org/
GDELT DOC documentation: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/

Broader web search endpoint: https://api.search.brave.com/res/v1/web/search
Docs: https://api-dashboard.search.brave.com/app/documentation/web-search

## Limitations / acceptance checklist

- An employer named in source metadata is not independently verified as the
  legal counterparty. A public job ad does not equal an outsourcing order.
- "All information about a company" is not an attainable guarantee;
  discoverability, robots/permissions, rate limits, naming collisions,
  paywalls and source reliability may prevent a full profile.
- Free GDELT news has a rolling ~3-month news corpus and a restrictive
  request budget; there is no guarantee any company will have matching news.
  GDELT is not an official business registry, cannot verify a legal entity,
  and cannot replace full-web discovery of corporate contact sites.
- Search vendor may return unavailable, 429, insufficient results; such states
  are explicitly shown, not hidden. No automatic retry after ambiguous CRM
  writes. GitHub scheduled runs have no strict 10-minute SLA.
- Real Bitrix readback must prove nine AI fields, provenance URLs, candidate
  contacts, professional manager advice, presence/absence of a legitimate
  score, owner task, the Kanban three-type title and two-user shared Kanban.
  Until then, the feature is integrated at CODE level and NOT live-accepted.
