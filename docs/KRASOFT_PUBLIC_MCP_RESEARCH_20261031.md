# КРАСОФТ — доступный без регистрации API поиска закупок

**Дата:** 31.10.2026. **Тип:** публичный API агрегатора, НЕ первоисточник B2B-Center/ONLINECONTRACT/SUPPMAN.

## Проверенная официальная документация
- https://zakupki.krasoft.ru/mcp — endpoint `https://zakupki.krasoft.ru/api/mcp`, Streamable HTTP, без авторизации можно обращаться с 20 поисковыми запросами и 50 карточками в сутки.
- https://zakupki.krasoft.ru/pricing — бесплатный доступ для гостя, лимиты.
- https://zakupki.krasoft.ru/integratsiya-api — отдельный REST API с OAuth client credentials, но он требует аккаунт и **здесь НЕ используется**.
- https://glama.ai/mcp/connectors/ru.krasoft/zakupki/tools/search_tenders — `search_tenders`, `query`, `regionCodes`, `pageSize`, `page`, `period`.
- https://modelcontextprotocol.io/specification — JSON-RPC / Streamable HTTP contract.

## Scope / technical implementation
`leadpilot/krasoft_public_mcp.py` uses the **published MCP POST endpoint**, negotiates the MCP session, and invokes **only read-only** `search_tenders`. Uses stdlib TLS checking, hard-coded endpoint, no redirect following, 12s timeout, max 1MiB per response, max 3 searches per operator invocation, <=10 per page, and removes raw result bodies from log output. A failed login, CAPTCHA, rate-limit, non-JSON response or unverified schema fails closed.

`scripts/run_krasoft_public_mcp_daily.py`: rotates three of the nine IT categories each day; performs the same search for regions 77 and 50 (6 searches/day vs 20 anonymous daily limit). Reports under Git-ignored `exports/`, no new dependencies.

`.github/workflows/krasoft-public-mcp-live.yml` attempts a daily read-only network probe and uploads **metadata-only** evidence. Scheduled GitHub Actions workflows will run only from the default branch after merge; initial push checks on feature branch may run. At inspection, the repository's prior Actions jobs were failing **before steps started**, so successful live execution **cannot be claimed until job actually runs**. Do not merge on synthetic unit tests alone.

## Data meaning
- An HTTP 200 MCP response can prove transport, **not** that a purchase is open and matches a real company.
- A returned item is from an **aggregator**, not necessarily a direct private commercial buyer request.
- Public 44-FZ/223-FZ procurement should become a *separate service opportunity queue*, NOT falsely count as a private FNS micro/small company.
- Qualification requires exact original source URL, date/deadline, actual legal buyer and INN (not auction organizer), FNS SME size and Moscow 77/Oblast 50, service fit, dedup, current clients and stop lists, contact channel and human review.
- A response schema is not assumed. The probe reports field names and raw row counts when clearly structured; otherwise reports SCHEMA_PENDING. No fake sample or assumed schema is counted as a qualified lead.

## Acceptance status
`PUBLIC_MCP_ENDPOINT_DOCUMENTED=YES`; `REAL_LIVE_CONNECTION_FROM_THIS_PROJECT=NOT_YET_VERIFIED`; `REAL_ORIGINAL_NOTICES_VERIFIED=0`; `FNS_TARGET_BUYERS_VERIFIED=0`; `QUALIFIED_REAL_LEADS=0`; `CRM_WRITES=NO`.

The current execution environment cannot resolve external domains to run this source. A production connection requires a genuine successful hosted run with 200 response and records, followed by implemented normalization, original-source verification and FNS + lead gates. Do not treat documentation, github action definition or offline tests as that evidence.

## Manual operational command (only if needed for future debugging)
Run in a checkout with Python 3.12:

```powershell
python -m leadpilot.krasoft_public_mcp --region 77 --query "сопровождение 1С" --page-size 5 --output exports/krasoft-probe.json
```

No registration, API key, paid subscription, Bitrix24, bids or outreach is required. Do not upload locally produced raw responses containing sensitive fields to GitHub.
