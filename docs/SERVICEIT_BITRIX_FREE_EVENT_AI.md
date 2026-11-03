# ServiceIT: AI automation inside Bitrix24 for each new card

Target: serviseit.bitrix24.ru; smart process entityTypeId 1062,
category 21. This is triggered by creation of ANY new card in Bitrix.
No Gmail, B2B-Center, source crawling or laptop-dependent polling.

## Free cloud architecture

Bitrix24 official outgoing ONCRMDYNAMICITEMADD webhook ->
private owner-only Google Apps Script web app -> HMAC-signed ID-only
GitHub repository_dispatch -> GitHub Actions -> exact Bitrix
crm.item.get -> approved GigaChat or honest provisional HOLD report ->
nine AI fields via crm.item.update -> exact readback for manager.

We do not use the unreliable crm.item.list. No company, contacts, INN
or raw CRM fields are transmitted to GitHub by the event bridge. For
public-title GigaChat, a separate corporate data-processing approval is
required. Unverified cards never receive invented Lead Score.

- Bridge source: integrations/bitrix_event_bridge/Code.gs
- Action: .github/workflows/serviceit-bitrix-new-card-ai.yml
- Processor: leadpilot/serviceit_bitrix_dispatch_worker.py
- Encrypted GitHub fence branch: serviceit-ai-encrypted-ledger
- Headless Codex PowerShell: scripts/RUN_SERVICEIT_BITRIX_AI_CODEX_HEADLESS.ps1
- Local protected installer: scripts/INSTALL_SERVICEIT_BITRIX_EVENT_BRIDGE.ps1

The Bitrix exact event is reserved in an encrypted GitHub CAS receipt BEFORE
any model or REST write; ambiguous response means REVIEW, never blind replay.
No stage, existing manager, contacts, tasks, outbound email/calls, historic
card #3 or deleted card #7 are modified.

## One command on the owner's Windows account

    $Repo = Join-Path $HOME 'Service_IT_GitHub_Repo\Service-IT-LeadGen'
    git -C $Repo pull --ff-only origin main
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Repo\scripts\RUN_SERVICEIT_BITRIX_AI_CODEX_HEADLESS.ps1" -Deploy

The Codex prompt is streamed via stdin (the final dash sentinel), from a
separate Git worktree. Codex receives NO DPAPI secrets and performs NO
corporate writes. The GitHub PR must pass CI and merge before the live
local installer runs.

## One-time authorized portal steps

1. Manager approves writing only the 9 AI fields of FUTURE process 1062/21
   cards; preserve all pre-existing CRM items and existing support pipeline.
2. The installer checks same-user DPAPI Bitrix REST credentials on a
   READ-ONLY scope and creates a NEW standalone owner-only Google Apps Script
   web app, leaving the Gmail scripts unchanged.
3. In the Google editor, Deploy -> New deployment -> Web app; run as owner,
   access Anyone. Verify the publicly reachable HTTPS /exec URL.
4. In Bitrix24 Developer resources > Other > Outgoing webhook: paste the
   new callback URL and select onCrmDynamicItemAdd or its 1062 variant.
   The portal generates a separate outgoing application_token. Paste that
   ONLY into the local hidden PowerShell prompt, NEVER into this chat.
   Without permission to create outgoing webhooks, ask corporate Bitrix
   manager for that narrowly-scoped access; REST URL alone cannot do it.
5. Activate the GitHub gate only after owner approval and smoke checks.
   Create ONE genuinely NEW approved test card 1062/21, paste its internal
   CRM card ID into the local prompt. The installer checks that all nine
   AI report fields were populated by the event workflow with exact
   crm.item.get readback. If not, it turns the cloud gate OFF, and does not
   blindly retry writes.
6. GigaChat is the primary model, but requires separate approval to send
   permitted data. Without independent source/INN/stop-list/legal
   checks, the truthful result is manager HOLD with an actionable report,
   not a fabricated verified Lead Score.

There is no need to keep the laptop on after setup. Free Apps Script and
GitHub Actions are best-effort and have no hard latency/SLA guarantees;
GigaChat API use may have its own quota/cost.

## Truthful readiness

GitHub CI and synthetic tests only prove code contracts. Real live
readiness requires registered Bitrix outgoing webhook, externally
reachable Google webapp, secure owner authorization and a true new-card
exact readback. Never claim production LIVE before that canary passes.
