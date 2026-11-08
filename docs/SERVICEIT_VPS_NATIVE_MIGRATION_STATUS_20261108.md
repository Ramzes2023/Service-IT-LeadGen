# Service IT — VPS-native migration state and safe activation (2026-11-08)

Scope: **internal automation only** for genuinely NEW smart-process
`1062` / category `21` cards at `serviseit.bitrix24.ru`. Source
ingestion is excluded by owner instruction. No changed SSH, RouterAI,
Bitrix, GitHub Secrets or cryptographic keys. No GigaChat provider.

## Code now available in PR #241 (NOT evidence of live VPS deployment)

| Native VPS process | PG / policy | Default |
|---|---|---|
| `serviceit_vps_worker` | 600-second poll, nine HOLD/AI field worker, native transactional one-use receipts, readback, seven-day outage alarm | READONLY |
| `serviceit_vps_native_aux` | Owner STOP, CRM-visible INN duplicates, three-signal Kanban native title, manager tasks, provisional Score OR free-MSP internal Score, FNS result field | all OFF |
| `serviceit_vps_native_msp` | Official monthly FNS-MSP public ZIP with bounded I/O to separate disk; PostgreSQL membership HOLD receipts only | optional profile, OFF |
| `serviceit_vps_native_business` | Manager-confirmed post-call next action and finance manager tasks, with versioned PG idempotency | all OFF |
| `serviceit_vps_native_observer` | Real read-only nine-field / task / score / funnel evidence, never estimates confirmed paid revenue | READONLY |
| `serviceit_vps_native_actions` | Separate one-use operation/scope/object/version PostgreSQL receipts | no external actions |
| `serviceit_vps_poll_checkpoint` | Durable polling heartbeat; blocks a silent gap beyond seven-day discovery coverage | fail closed |

Dockerfile explicitly copies only the empty owner STOP rules from
`config/serviceit_own_signal_stop.json` rather than assuming it exists
inside the image. The full Compose needs only existing pg_password,
bitrix_webhook and RouterAI key; optional Brave key is not required.
Free official FNS-MSP is not the paid EGRUL/EGRIP service.

## What has NOT been demonstrated

- No successful full **production** new-card -> 9 AI fields -> task/score/title
  write+readback on VPS. CI synthetic tests are not production validation.
- The official FNS dataset has not been successfully streamed on this VPS
  and the optional `msp` service is deliberately not enabled.
- Post-call and financial functions have not been proven with real
  manager-approved call and real paid deal; no payment is authorized.
- Public site allowlists / legally approved RouterAI data disclosure must
  be verified on VPS. A browser sandbox with JS rendering/Playwright is
  **not yet deployed**; public HTTPS, GDELT and optional Brave are only
  HTTP-based research. Don't claim browser interaction is available.
- Full CRM visibility for corporate STOP and cross-customer dedup is
  **not proved** by a scoped REST webhook. UNKNOWN never equals CLEAR.
- Formal evidence-backed Lead Score and any external-contact authorization
  remain separate corporate approvals.
- Native Telegram/WhatsApp, Beeline PBX, all sales documents and bank
  matching need real authorized service-provider integration. The system
  can stage templates and internal tasks but cannot invent invoices or bank
  confirmations.
- Preexisting GitHub writing workflows are STILL active. Cutover cannot
  be declared merely from green unit tests or `VPS_CUTOVER_ACTIVE=NO`.

## READONLY staging, safely independent of old gateway

A new owner-only workflow
`.github/workflows/serviceit-vps-native-shadow-deploy.yml`
runs only by explicit `workflow_dispatch` input
`SHADOW_READONLY_ONLY` from main. It verifies pinned SSH host,
refuses root, SHA256-validates the release and refuses any live write gate.
It stages coordinator, auxiliaries, business and observer in a separate
Compose project, **without a second HTTP ingress** so the old
two-secret readonly gateway on port 18780 remains intact. It leaves all
GitHub Actions production writers unchanged. It runs additive PostgreSQL
schema migrations; does not delete or rewrite legacy records.

After merge to main, the authenticated repository owner can trigger:

```powershell
gh workflow run serviceit-vps-native-shadow-deploy.yml --repo Ramzes2023/Service-IT-LeadGen -f approval=SHADOW_READONLY_ONLY
```

Check the resulting workflow run/log and VPS Compose status. Do not
assume that a successful upload alone proves that four native containers
are healthy or that a real write occurred. If SSH or VPS configuration
is unavailable, the deployment must fail rather than silently use
a password/fallback connection.

## Final migration / permanent cutover is a DIFFERENT operation

1. Confirm independent authorization for model disclosure, actual
   employee/task permissions, full CRM scope, and approved public domains.
2. Confirm the full VPS shadow READONLY run: Bitrix positive-control #3
   GET, strict last-10-minute list, exact native PG migrations, real
   RouterAI synthetic API test, STOP/Score/Task preflight.
3. Audit **all** old GitHub writer workflows and in-flight runs, not just
   the two AI workflows. Stop each independently and verify completion.
4. Record the first allowed future UTC timestamp in
   `SERVICEIT_VPS_NATIVE_ACTIVATION_UTC` only **after** writers are stopped.
   Never re-use the historical cloud activation timestamp.
5. Enable one server module at a time with live-permitted gates
   (`SERVICEIT_VPS_WRITER_MODE=LIVE`,
   `SERVICEIT_VPS_CUTOVER_AUTHORIZED=YES`,
   `SERVICEIT_VPS_OLD_GH_WRITERS_DISABLED_ACK=YES`,
   `SERVICEIT_VPS_POLL_ENABLED=YES`) plus independent module opt-ins.
   No source ingestion. No historical backfill. No silent retry after
   an uncertain write. STOP/duplicates are evaluated before creating tasks.
6. Verify a newly created authorized signal end-to-end, exact nine AI
   fields and the right task/title/Score, with actual CRM readback.
   Verify all three signal types and a versioned finance/post-call event
   separately, under manager confirmation.
7. Enable recurring services with watchdog alerts and backup recovery
   only after exact production acceptance.

If the seven-day polling recovery window is exceeded, the native
worker blocks to prevent silently missing cards. It is NOT safe to
move activation backwards or simply generate a fresh Ledger key.

**Current claim:** code + isolated CI/PG tests available; PR staged.
No cutover and no confirmation of full production business automation.
