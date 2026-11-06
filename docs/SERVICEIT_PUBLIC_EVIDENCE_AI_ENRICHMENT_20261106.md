# ServiceIT — public evidence research for AI cards (2026-11-06)

## Applies to

Real **new** smart-process 1062 / category 21 cards handled by the
existing signed Bitrix event worker and scheduled all-new batch fallback.
No lead sources, companies, contacts, deals, stages, tasks, old cards 3/7,
global deduplication or formal score are changed by this feature.

## Activation / scopes

- Both active workflows for new-card AI explicitly set
  SERVICEIT_PUBLIC_RESEARCH_ENABLED=YES.
- Existing privacy approval SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES
  is required before research; existing preliminary-title AI toggle,
  provider credentials and permissions remain separately required for AI inference.
- SERVICEIT_PRELIMINARY_PUBLIC_HOSTS is the permitted source hostname list.
  Research uses this as the initial allowlist.
- An owner can add approved company domains to GitHub Actions variable
  SERVICEIT_PUBLIC_RESEARCH_COMPANY_HOSTS (comma-separated).
  Unknown company hosts are never crawled.
- No paid web-search API is needed. This is **bounded public source/site browsing,
  not an internet-wide search engine**: it cannot automatically discover a
  company's website if the job posting never links it or if its host is not
  approved. Do not guess unavailable email addresses or phone numbers.

## Enrichment

1. Read a public source URL from the allowed HTTPS host, respect robots rules,
   reject redirects and private DNS, cap response size and timeouts.
2. If the source explicitly links an "official company website" or "company
   website" candidate, visit it only when that host is approved. A link on a
   marketplace is not independent proof of the company's legal identity.
3. Check at most five public pages including labeled contacts, company info,
   careers or requisites pages.
4. Extract only published business-domain email and phone numbers from the
   candidate company site; record exact source URL and check timestamp.
5. Put sourced findings, role-based tender/vacancy/direct-service guidance and
   a manager-only communication draft in the existing nine AI fields.
6. The AI model may read a short contact-free public excerpt under the
   existing privacy gate; its quoted evidence must match the exact source.
   Never manufacture contacts, deadlines, lawful outreach permission or score.

## Safety and acceptance

- Preserve the encrypted one-use receipt, exact Bitrix identity, before-image
  fingerprint and exact nine-field readback from the existing worker.
- Previously populated nine HOLD fields are augmented only when new
  public evidence is available; otherwise they are preserved unchanged.
- No company/contact creation, automatic outreach, additional lead source,
  task creation or changes to STOP/dedup/Lead Score code.
- GitHub cron is best effort, not a guaranteed ten-minute SLA.
- CI is offline: success does not prove that the real Bitrix new-card path ran.

After the first real source is connected, perform a complete real-card readback
for tender, vacancy and direct-service cases: all nine AI fields; found/missing
contacts with origin; correct recipient and message; AI/score status; icon and
manager task. Independently verify common Kanban view using two employee
accounts. This user-acceptance evidence is still required.
