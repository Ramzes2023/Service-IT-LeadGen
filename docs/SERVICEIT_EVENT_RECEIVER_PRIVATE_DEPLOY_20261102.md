# Private deployment pack: Bitrix24 ServiceIT new-card event receiver

Scope only: isolated smart process entityTypeId 1062, category later checked by PR #75 read-only worker. This receiver records IDs in a durable queue; it does not invoke real GigaChat or write to Bitrix.

Files:
- Dockerfile.serviceit-event-receiver — Python 3.12, minimal files copied, UID/GID 10001, disabled access log.
- deploy/serviceit-event-receiver.compose.yml — binds **127.0.0.1:18780**, non-root, read-only container root filesystem, persistent named volume serviceit_event_queue_private, no-new-privileges, all Linux capabilities dropped, healthcheck.
- .dockerignore — credentials, local databases/logs, internal docs and artifacts excluded from Docker context.
- scripts/TEST_SERVICEIT_EVENT_RECEIVER_DOCKER.py — CI synthetic POST, duplicate, invalid application token; never uses real Bitrix.

## Operator gates, not yet performed

1. Administrator approves an owned server/infrastructure, TLS domain, callback credentials, model-data privacy policy and backup. Do not use VALYQON's public server by implication: this is a separate corporate deployment.
2. Provision a dedicated **Bitrix event application_token** for this exact corporate portal; existing inbound REST webhook is not interchangeable. Store token securely **on the server**, not in GitHub, issue comments or plaintext shell history.
3. Configure TLS reverse proxy for a restricted public callback URL that forwards only /integrations/serviceit/bitrix24/new-card to 127.0.0.1:18780. Add upstream request limits and per-IP throttle.
4. On the authorized server, set SERVICEIT_BITRIX_APPLICATION_TOKEN using approved secrets management. docker compose will refuse startup if missing.
5. Deploy using the private Compose file. Start only after checking persistent Docker volume permissions for UID 10001, health and ingress TLS. No production writer is enabled by this deployment.
6. Register the official Bitrix24 ONCRMDYNAMICITEMADD event **only after** HTTPS endpoint and authenticating callback are verified and tenant admin has approved. Never call event.bind against an unready endpoint; use official docs. Application installation must be complete.
7. Verify one synthetic new-card event and duplicate, then real authorization tests, only with authorized synthetic/isolated data. Monitor queue backlog, keep volume backed up. The queue never stores access tokens or raw source/company contents.
8. Run and accept the PR #75 read-only worker with a *separate* read-enabled webhook, then finish trustworthy source evidence / global customer dedup / FNS/INN / GigaChat privacy eligibility and bounded CRM one-shot update/readback.

## Boundaries

Do not imply the source's historical item B2B-Center #4661617 is auto-enriched by this deploy, or that the model automatically runs for every lead. There is **no live deployment** or verified automatic item update under this PR. The queue and read-only worker are infrastructure toward that goal.

No CRM production modifications; no customer contact; old two badge-ledger UNCERTAIN entries for DEMO #3 not touched. An accidentally public 0.0.0.0 listener is not acceptable, hence explicit localhost port binding.

Official documentation:
https://apidocs.bitrix24.ru/api-reference/crm/universal/events/on-crm-dynamic-item-add.html
https://apidocs.bitrix24.ru/api-reference/crm/universal/events/index.html
