# ServiceIT native core cutover — operator procedure (2026-11-08)

Status: **staged for CI review. NOT evidence that LIVE is enabled.**

This one-shot transfer is limited to real future **Bitrix24 smart-process
1062/category 21** cards on `serviseit.bitrix24.ru`. User-approved
internal task recipient: **Olga Torgashina, Bitrix user 605**.
The owner's personal STOP remains an empty list, as requested.

## Previous acceptance evidence

- VPS: four native `coordinator`, `auxiliaries`, `business`,
  `observer` containers healthy with PostgreSQL 16 and READONLY gates.
- Real synthetic RouterAI chat from VPS: HTTP 200, complete JSON,
  no corporate data.
- Read-only Bitrix metadata: nine fields and positive-control card #3
  confirmed. Native queue was empty in operator's prior report.
- Bitrix `user.get` verified active Olga 605 and task binding.

## What one-shot ACTIVATION would enable

1. Native coordinator: strict 600s ID-only Bitrix polling + future-card
   nine manager AI/HOLD fields. RouterAI only for titles from explicitly
   approved public source hosts; no GigaChat fallback.
2. Native auxiliaries: personal STOP logic (empty rule list), visible
   CRM duplicates, three-signal Kanban titles/badges, internal review
   task for Olga 605 after an AI receipt, **provisional** Score only.
3. Native observer: hourly factual new-card/task/score readback.
4. Native business: containers remain healthy but post-call and finance
   writers stay OFF until a separately approved real manager action.
5. Existing external source flows remain in GitHub, still allowed to
   create source-linked HOLD cards. Their optional GigaChat is disabled
   by `SERVICEIT_VPS_CUTOVER_ACTIVE=YES`.

Not enabled: unrestricted corporate score, full CRM global STOP clearance,
free FNS-MSP bulk ZIP (requires storage and dataset verification), finance,
postcall, client outreach, bank/payout integrations, unapproved website
navigation, optional Brave subscription, Chromium/Playwright browser.

## Strict prerequisites

1. Main GitHub code is reviewed and tests pass. Operator launches the
   script from an authenticated PowerShell with `gh`, `ssh`, and the
   existing pinned SSH key.
2. Operator approves EXACT public publisher domains. The sample
   allowlist is a starting proposal, not a blanket permission for private
   CRM/company data disclosure. Only publicly posted signal titles can
   be transmitted to RouterAI.
3. A new owner-only READONLY shadow deploy loads the latest reviewed
   main commit on the server. No production write at this stage.
4. Both READONLY probes prove four healthy services, empty recent
   pre-cutover window and native queue, verified Olga 605, AI privacy
   approval and personal STOP empty.
5. No old overlapping GitHub CRM writers are in progress / queued.
   The operator writes `SERVICEIT_VPS_CUTOVER_ACTIVE=YES`, checks it
   reads back, and re-checks for in-flight jobs. Existing source intake
   stays untouched.
6. Time immediately before that GitHub cutover flag is captured as
   `SERVICEIT_VPS_NATIVE_ACTIVATION_UTC` to include cards that land
   during handoff. Live begins only after all old writers have stopped.
7. Server-only script does atomic 0600 private config backup and update,
   Docker Compose reload of four services and each health check.
   If anything fails it tries reverting local VPS to READONLY. The
   GitHub cloud fence remains YES in all failure cases; no blind rollback.

## PowerShell use

On a workstation containing a clean copy of this reviewed file:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\SERVICEIT_VPS_FINAL_CUTOVER.ps1 -Mode PREVIEW

# Only after reading the preview and confirming the source host allowlist:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\SERVICEIT_VPS_FINAL_CUTOVER.ps1 -Mode ACTIVATE
```

The default `SourceHosts` in the script is a proposed set of public
IT/procurement publishers from the existing project; operator may use
`-SourceHosts` with a smaller approved list. The script prints no
Bitrix webhook, API keys, raw customer text, CRM IDs or retrieved
website excerpts.

## First LIVE acceptance

Make exactly ONE owner-approved **new** test/production HOLD card AFTER
native activation. Never repurpose historical cards (IDs 3/7).
Verify via readback all nine AI fields (HOLD allowed if independent
verification insufficient), task linked to that exact card with
RESPONSIBLE_ID 605, three-signal classification, duplicate and STOP
checks and preliminary Score flag. Monitor first two 600-second
cycles and log cases with `UNCERTAIN`. Never blindly re-run a
possibly completed CRM mutation. A successful cutover tool exit is
not proof of every business function or new source being fully working.

If no source produces a real eligible card, the LIVE worker may be
healthy without having written any AI fields or tasks yet. Do not
fabricate success statistics.
