# ServiceIT — owner-approved VPS internal task assignee: Olga Torgashina (605)

2026-11-08. Owner specifically requested that the personal STOP list remain
empty and that Olga Torgashina be the temporary recipient of automatic
internal review tasks in Bitrix24.

## STOP list

`config/serviceit_own_signal_stop.json` retains its original `"rules": []`.
Never fabricate entries. An owner may separately add a reasoned STOP entry
later. Any corporate STOP / global customer-dedup is separate from this
empty personal list and must remain fail-closed where relevant.

## Task routing

- Target is the owner's portal `serviseit.bitrix24.ru`, user ID `605`.
- Only VPS-native `TASK_V1` processing after independent cutover uses the
  scoped recipient when both nonsecret flags are configured:
  `SERVICEIT_VPS_TASK_ASSIGNEE_APPROVED=YES_1062_21_INTERNAL_MANAGER`
  `SERVICEIT_VPS_TASK_ASSIGNEE_ID=605`.
- The `task_recipient_for_card` function affects ONLY the task's
  `RESPONSIBLE_ID`; it never changes the original card's `assignedById`.
  Thus an assigned card can still generate a review task for Olga, without
  transferring the actual CRM record to her.
- The previous GitHub cloud task logic is unchanged until cloud writers
  have been stopped and VPS cutover accepted.
- New card types are automatically triaged as tender/vacancy/service as
  available in CRM metadata, not invented. Task creation is internal and
  requires exact native AI receipt, all corporate safety gates and a
  confirmed active employee. No outbound customer contact is performed.
- An existing visible linked task does not trigger a duplicate. Uncertain
  outcomes are not blindly replayed. Cross-scope global task visibility
  remains unverified when Bitrix webhook does not expose all tasks.

## Read-only preparation

Run the owner-only workflow on `main`:
`serviceit-vps-olga-task-readonly-prepare.yml`, input
`approval=PREPARE_OLGA_605_READONLY`.

It uploads a checksum-pinned script via SSH, verifies real Bitrix user 605
is an active employee with exactly expected first and family name, and
reports task link/permissions as VERIFIED or UNVERIFIED. If identity is
unverified the workflow fails before editing VPS configuration.

It then saves ONLY the two nonsecret task-recipient flags to an
owner-only `vps.env`, with a private backup, while requiring:

- `WRITER_MODE=READONLY`
- `CUTOVER_AUTHORIZED=NO`
- `POLL_ENABLED=NO`
- `MANAGER_TASK_ENABLED=NO`
- no earlier native activation timestamp or old GH disabled ACK.

The workflow does not restart containers. Only a subsequent
`serviceit-vps-native-shadow-deploy.yml` `SHADOW_READONLY_ONLY`
can load the updated values; that deployment still cannot write tasks.

## Future acceptance

Before any live task creation verify a fresh task-link metadata and
`tasks.task.add` permission with separate approval; later create exactly
one new future card after proper cloud drain; confirm its nine AI fields
and the exact task responsible ID 605 and CRM link by REST readback.
Do not attempt a task on historical cards merely to demonstrate this.
