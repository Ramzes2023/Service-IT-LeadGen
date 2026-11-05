# ServiceIT AI primary / secondary cutover — 05.11.2026

**Owner instruction:** DeepSeek V4.1 Flash via RouterAI becomes the primary production AI for *future, genuine* Bitrix24 1062/21 cards. GigaChat is kept secondary for failed/low-quality DeepSeek calls. Do not remove its credentials or TLS roots yet.

## Successful API attestation
- Real RouterAI synthetic authenticated request and AI output validation: [GitHub run 42034007218](https://github.com/Ramzes2023/Service-IT-LeadGen/actions/runs/42034007218), `ROUTERAI_API_AND_AI_VALIDATION_PASS` after fixing protected GitHub secret.
- No client data or CRM writes were sent during the attestation.

## Live implementation

Both primary CRM workflows `.github/workflows/serviceit-bitrix-new-card-ai.yml` (signed new-card events) and `.github/workflows/serviceit-bitrix-all-new-batch-ai.yml` (future new cards every ten minutes) select:
- `routerai` first, only if **`SERVICEIT_AI_PROVIDER=routerai`**, **`SERVICEIT_ROUTERAI_PRIVACY_APPROVED=YES_1062_21`**, and the RouterAI API key secret is present. The Python model adapter also checks its documented `sk-` format and requires `SERVICEIT_WORKER_MODEL_PRIVACY_APPROVED=YES`. The user-approved production code cutover follows the successful authenticated API test. The old extra manual variable `SERVICEIT_ROUTERAI_LIVE_READY` is no longer required for these two exact production workflows.
- Otherwise `gigachat` remains selected; a rollback is possible by setting the repository Actions variable `SERVICEIT_AI_PROVIDER=gigachat`.
- When RouterAI is primary, on **one exception or an unusable classification** only, the exact-item worker uses the already prepared GigaChat provider **once** for the same already-authorized text (no extra card fields, no new lead source, no independent CRM writes). If GigaChat also fails, the result is `NEEDS_REVIEW` or a truthful HOLD according to the existing exact-item safeguards, **not** a fabricated verified lead.
- Both workflows construct the verified application-local GigaChat CA bundle on each run. TLS verification stays ON; no `verify=False`, no global CA changes.
- Models are called only after existing strict independent corporate evidence authorization or the public-title hostname allowlist. Private CRM fields, full documents, phone numbers or internal notes are **not** blindly sent. Existing signed bridge, encrypted one-use fencing, exact REST readback, nine AI fields, and no replay on ambiguity are retained.

## Evidence and limits

An exact read-only, no-token routing gate `serviceit-routerai-primary-readiness.yml`, initiated by **`SERVICEIT_ROUTERAI_PRIMARY_STANDBY_READONLY_ONCE_V1`** as a new comment to Issue #148, prints only sanitized booleans: selected provider, key shape, both privacy gates, presence of GigaChat credentials and pinned CA. It cannot extract secret values or customer data.

**An actual new card still needs to arrive to prove an end-to-end CRM analysis.** This change must not create a fake card, revisit old cards, enable source collectors, bypass STOP/FNS/duplicate checks, contact clients, or claim final qualified Lead Score.

## Removal of GigaChat later

Only after RouterAI has processed genuine new cards successfully, provider availability and data quality have been observed long enough, and the company has agreed a replacement for continuity: set fallback to `NO` in a later reviewed deployment and remove the old GigaChat secret. **Do not remove it in this cutover.**
