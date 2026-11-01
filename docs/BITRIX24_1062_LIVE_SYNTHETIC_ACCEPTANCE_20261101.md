# Service IT — live Bitrix24 synthetic acceptance, 01 November 2026

## Scope and operator evidence

The owner executed the self-contained V4 guarded **synthetic-only** Bitrix24 integration operator on the corporate portal `serviseit.bitrix24.ru`. Its console output reported:

```text
SERVICEIT_1062_LIVE_INTEGRATION=SELECT_PROBE_V4_START
SCOPE=ONE_TEST_SMART_PROCESS_1062_CATEGORY_21; MAX_TWO_SYNTHETIC_ITEMS
FILTER_NEGATIVE_CONTROLS=REQUIRED; FULL_16_FIELD_READBACK=REQUIRED
PRE_LIVE_SYNTAX=PASS
SERVICEIT_BITRIX_LIVE_1062=PASS
ENTITY_TYPE_ID=1062; CATEGORY_ID=21; FIELDS=16; STAGES=6
SYNTHETIC_CARD_ID=3; STAGE=DT1062_21:CLIENT
SYNTHETIC_CARD_ID=5; STAGE=DT1062_21:PREPARATION
REAL_LEADS=0; SOURCES=OFF; CUSTOMER_CONTACT=NO
SERVICEIT_1062_VISIBLE_IN_PORTAL=PASS
```

The owner independently supplied a **corporate Bitrix24 UI screenshot** showing the live synthetic item `DEMO ServiceIT AI — DEMO-ITREQ-001`, stage **Проверка менеджером**, fields containing the demo INN, demo company, region, signal ID/date and synthetic source, and a visible stage-change history. The default panel `Связаться с клиентом` was open but not shown as saved; the operator should *not* save it or trigger customer outreach.

Verified links, accessible only with suitable corporate permission:
- [Synthetic card #3 — manager review](https://serviseit.bitrix24.ru/crm/type/1062/details/3/), `DT1062_21:CLIENT`.
- [Synthetic card #5 — company check](https://serviseit.bitrix24.ru/crm/type/1062/details/5/), `DT1062_21:PREPARATION`.

The V4 operator resolved the earlier `crm.item.list` readback failure where the selected candidate lacked `id`; earlier V1/V2/V3 blocks are historical and **do not negate** this successful operator confirmation. Do not repeat creation just to reproduce a success. Preserve its durable local state and sanitized result at `%LOCALAPPDATA%\ServiceIT\Bitrix-1062-Live-Manager-Demo\result.json`.

## What this proves

- Portal smart process `entityTypeId=1062`, `categoryId=21` is available to the authorized webhook user.
- 16 expected custom fields and six configured stages were validated by the operator.
- Two synthetic cards are actually present and visible via real CRM URLs, not only HTML/mocks.
- The offline project suite previously passed `561 passed, 26 subtests passed` at commit `c277be389def6f8c9f891716940631764b7fdccb`; the V4 operator package is external to the PR and was not part of that 561-test run.

## What this does NOT prove

- Not a production service, source-driven lead pipeline, automated scheduler, or automatic CRM sync worker.
- No real purchase leads, contracts, real company contact, e-mail/SMS or calling permissions, customer records, or other eight CRM funnels were exercised or authorized.
- A local manager-role label does not prove Bitrix24 corporate RBAC. Approval to change real records is still separate.
- API results are operator evidence; no independent tool-based real REST readback was performed by the GitHub assistant in this documentation update.
- Remote uniqueness, lifetime policy/consent status, monitoring, deployment, rollback and authorized production integration are not certified.
- PR #36 is a draft targeting `main`; PR #37 is a draft based on PR #36's branch; PR #38 is a draft based on PR #37's branch. Therefore **the code is not merged into main** and no production release was deployed.

## Handoff to Mikhail

For a safe manager demonstration, open card #3 and card #5 in the corporate portal and review the custom fields, two different triage stages and recorded history. Call them *synthetic demo signals*, not real client requests, and avoid clicking `Сохранить` in the default `Связаться с клиентом` activity panel.

**Acceptance: LIVE_SYNTHETIC_1062=PASS; PRODUCTION_READY=NO; SOURCES=OFF.**

Remaining non-source work: controlled review/merge of stacked PRs #36→#37→#38, deploy a durable worker only after explicit corporate permission and identity/stage/field/duplicate gate review, confirm RBAC and disabled automation policy, document monitoring/runbook and production rollback. Real source onboarding is a separate milestone.
