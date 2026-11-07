# Service-IT VPS — RouterAI only (2026-11-08)

Operator direction: Do not connect GigaChat to the corporate VPS.

## Implemented in the full VPS Compose (not yet deployed)

- Exactly one AI provider: RouterAI.
- Hardcoded `SERVICEIT_AI_PROVIDER=routerai`.
- Explicit `SERVICEIT_ROUTERAI_FALLBACK_TO_GIGACHAT=NO`.
- No `gigachat_credentials` secret, no GigaChat scope/model configuration,
  no GigaChat certificate initialization or forced CA bundle.
- In the VPS worker environment adapter, even an inherited credential or
  fallback flag is ignored and replaced with an empty credential and `NO`.
- An alternate VPS provider is rejected before exact-card processing.
- The standalone existing two-secret READONLY Compose is UNCHANGED.

## Actual verified server state

- Docker ingress + coordinator in two-secret READONLY Compose: healthy.
- `/health` over public HTTPS: 200 and `crm_writer_enabled=false`.
- PostgreSQL password, Bitrix REST webhook, GitHub ledger PAT and RouterAI
  model API key present: **4 secrets**.
- GitHub ledger PAT can read repository and encrypted-ledger branch.
- RouterAI direct authenticated synthetic completion from VPS: PASS.
- Five previously missing secret files now become FOUR required for this
  **eight-secret RouterAI-only VPS Compose**:
    1. `bitrix_app_token` (official Bitrix outgoing event token)
    2. `event_bridge_key` (shared with current GitHub event handlers)
    3. `gh_ledger_key` (same Fernet encryption key as active cloud ledger)
    4. `brave_api_key` (approved external research key)

Existing GitHub Secrets remain unchanged; the owner does not want GigaChat
on VPS. These changes do not delete or rotate any cloud GigaChat key.

## Important operational safeguards

This PR alone **does not** install new code onto the running container.
The image was built at an earlier commit. Do NOT switch to full Compose,
enable events, write to Bitrix24, or disable old GitHub workers until the
remaining four authentic credentials are present, shared keys match the
cloud, the full image is rebuilt in a reviewed release, and an approved
one-new-card test establishes exact readback with no duplicate writer.

The VPS routerai key was validated with a minimal request without CRM data.
Any actual customer data transmission must still satisfy documented privacy
gates. If RouterAI fails, the VPS marks the item for review, and never tries
GigaChat. No automatic ambiguous retry.
