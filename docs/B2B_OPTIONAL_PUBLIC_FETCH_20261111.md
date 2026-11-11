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
