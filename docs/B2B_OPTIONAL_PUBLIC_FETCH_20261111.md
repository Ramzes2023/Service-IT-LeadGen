# B2B source fallback experiment — 11 November 2026

This is an isolated, **disabled-by-default**, read-only code candidate.
It does not alter the VPS coordinator, polling or Bitrix24 writer.

## Observed accessibility (real public reads)

- B2B-Center tender #4673971: successfully extracted primary public tender
  text using a separate cloud fetcher. Contact information and documents
  remain gated behind site registration.
- Tendum #4673595: successfully extracted a full public tender text.
- Kontur #4673971: cloud fetch timed out; the VPS previously saw HTTP 403
  on Kontur robots. A browser alone does not solve this.
- Synapse #4673595: cloud fetch received HTTP 403.

These experiments do not prove permanent accessibility or any change to VPS
source permissions.

## Code and boundaries

File: leadpilot/serviceit_b2b_remote_public_fetch.py

Method: fetch_remote_public_evidence(client, urls, tender_id, subject, api_key).

The caller must pass already-discovered, approved *public detail* URLs; at
most two requests. Only Tendum and the official public B2B-Center detail URL
patterns are allowed. The provider endpoint is fixed. Kontur is excluded
because the correct route there is an authorized API, export or email feed,
not routing around a denied origin.

Response is source evidence for later AI review. It contains checked
timestamp, source URL and verbatim extracted passages, but NO company
registry verification, no invented INN, no contact claims and NO CRM writes.
The original/public source number and subject are independently checked
against the full extracted page; search snippets and redirects never pass.

Use a separately issued server-side TinyFish API key stored as a Docker
secret. ChatGPT plugin authorization is NOT a reusable VPS API key.
Never commit secrets, cards or customer data to GitHub.

## Remaining deployment steps

1. Review test and data-source licensing, select approved public URLs via
   the existing discovery stage, and configure the separate server key.
2. Wire the adapter into the existing coordinator as an opt-in fallback for
   eligible failures; never launch a second independent CRM writer.
3. Persist source URL, checked_at, content digest and the verified excerpt
   in the protected PostgreSQL evidence ledger. Deduplicate by tender ID
   and source before AI analysis, add budgets and retry/backoff.
4. Pass verified evidence to the current RouterAI enrichment queue and
   use only its single-writer Bitrix24 safe-update/readback workflow.
5. Run no-write diagnostics on the four real cards and an explicitly
   authorized one-card write/readback acceptance before rollout.

This PR itself does NOT satisfy live VPS deployment, new-card automation or
Bitrix24 acceptance. All core work in parallel chats remains untouched.


## Free-only versioned additions

The optional provider publicly states that **TinyFish Search and Fetch are
free within published limits**; paid Agent, Browser and Monitor are NOT used:
https://www.tinyfish.ai/blog/search-and-fetch-are-now-free-for-every-agent-everywhere

- discover_free_public_urls() performs one fixed-host Search query by the exact
  source-published tender number and title, limited to approved public
  Tendum/B2B origins. The search index is NEVER evidence.
- free_public_search_and_fetch() passes at most two approved URLs to the
  existing Fetch adapter, or skips Search when the original source message
  supplies a reviewed exact public link.
- The adapter also returns structured_facts from the verified full-page text.
  The deterministic extractor runs without any extra API or LLM calls.
- extract_tender_facts() distinguishes buyer from marketplace organizer,
  published budget from absent budget, delivery place from company legal
  address, true submission deadline from generic listing cutoff, and public
  documents from login-only documents.
- attach_corporate_profile() requires an independently confirmed corporate
  website and matching published legal name. It checks INN / OGRN digits and
  records source URLs for publicly displayed address, business phones and
  emails. This remains SELF-REPORTED, not official FNS/registry verification.
- If no public sources are available, no buyer/INN/contacts/budget is invented.

This module does not scrape the corporate website on its own. The existing
independent company research or approved FNS/open-data validator must furnish
the real website text; matching a business's name in web search is not enough.
Commercial vendor databases and anonymous B2B scraping are not prerequisites.
The source operator must verify that using each third-party fetch is permitted.

## Zero-new-cost rollout gates

1. Create a separate **free** Search/Fetch API key at
   https://agent.tinyfish.ai/api-keys (not a paid browser/agent key).
   Set a strict vendor-side request budget; do not enable wallet auto-reload.
2. Store the key as a private Docker secret on the SERVICEIT VPS. Do not put
   it into a GitHub repository, Bitrix custom field, workflow log, or chat.
3. Review data-source terms / robots. The source read fails closed on denied
   origin access. Unavailable Kontur and Synapse remain unavailable.
4. Connect only the new read-only evidence result to the *current* V19/V20
   coordinator after reviewing its exact live SHA and PostgreSQL tables.
   Existing RouterAI and the single Bitrix writer remain unchanged.
5. Add a per-card budget, immutable deduplicated source receipt, and a
   separately authorized exact CRM write/readback. Do not replay any
   POST_UNCERTAIN, change HOLD, tasks or scoring, or claim live deployment
   before independent readback of the four production records.

Automated production usage is **not enabled by this PR**. In particular,
ChatGPT's connected TinyFish plugin credentials cannot be exported to VPS.
