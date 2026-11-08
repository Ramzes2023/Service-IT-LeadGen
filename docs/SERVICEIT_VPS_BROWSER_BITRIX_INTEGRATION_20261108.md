# Service IT — guarded Chromium → RouterAI → Bitrix24 LIVE integration

## Scope

Only future cards in Bitrix smart process 1062 / category 21, already discovered
by the existing ten-minute PostgreSQL-fenced coordinator.

- Sources and the other three LIVE VPS workers remain unchanged.
- No new source collectors, logins, outreach, finance, FNS or historic replay.
- For an owner-approved public HTTPS source URL, existing research_public
  tries bounded HTTP fetch first. If it fails or yields a minimal JS shell,
  and ONLY if both browser flags are YES, it requests that public page from
  the localhost browser bridge 127.0.0.1:18785/render.
- Browser receives ONLY the approved public URL, never Bitrix cards, CRM
  secrets, company private files or model prompts.
- Chromium returns max 6000 characters and labels it unverified. Existing
  AI builds nine AI/HOLD fields and a manager-facing referenced report.
- DeepSeek/RouterAI stays in the native coordinator. Brave Search API remains
  an independent optional integration and is not implied by Chromium.

## Security

The separate serviceit-public-browser-vps Docker Compose project has:

1. Browser: nonroot, read-only, cap-drop-all, Chromium sandbox REQUIRED,
   Docker internal:true network ONLY, no direct Internet route, no Docker
   socket and no CRM/PG/RouterAI/GitHub secrets.
2. Egress: nonroot HTTPS CONNECT proxy on both the internal bridge and its
   own external bridge. Approved HTTPS hosts only, port 443 only. All DNS
   answers must be public, and each outgoing socket connects directly to
   the validated IP, preventing post-validation DNS rebinding.
3. Browser bridge port is host 127.0.0.1:18785 only, never public.
   URLs, redirects, resources and robots rules are independently checked.
   No forms, login, contacts, purchases, downloads or cold outreach.
4. Coordinator browser flags default to NO. Legacy cloud workers cannot
   activate this feature; no double CRM writer is introduced.
5. On a failed health, sandbox, direct-egress, proxy or genuine public-page
   render test, integration activation aborts BEFORE touching coordinator.
   On core update failure the owner-only rollout restores original env and
   coordinator image, and shuts down browser sidecars.

Public HTML text is UNTRUSTED INPUT, not operational instructions. Website
claims do not prove legal identity, authorized contact or purchase intent.

## Owner-only LIVE integration deployment

After CI success and review/merge to main, trigger the manual workflow at:

.github/workflows/serviceit-vps-browser-live-activate.yml

Inputs:
- approval: ACTIVATE_ISOLATED_CHROMIUM_PUBLIC_RESEARCH_1062_21
- smoke_site: hh.ru, or trudvsem.ru / superjob.ru if their public root page
  and robots rules can be read.

Workflow sends a SHA256-pinned main archive via pinned SSH, builds a separate
browser image, starts browser/proxy sidecars, verifies real Chromium sandbox
rendering, blocked direct public-IP access, host-only bridge and one true
robots-respecting public page. Only then it rebuilds and replaces coordinator
alone. The other three native workers remain healthy and are not redeployed.
It never repeats the GH→VPS cutover or changes source ingestion.

Successful deployment messages:
- BROWSER_PUBLIC_RENDER_SMOKE=PASS
- SERVICEIT_BROWSER_AI_BRIDGE=LIVE_CONNECTED
- CORE_FOUR_HEALTHY=PASS
- REAL_NEW_CARD_E2E=NOT_YET_VERIFIED

After deployment still validate one genuinely new authorized test card:
nine AI/HOLD fields, URL evidence, Kanban, CRM duplication/STOP checks,
provisional Score, Olga 605 task and exact source/ledger readback.
Never replay an ambiguous CRM write.

## Known limitations

- Brave Search key not mounted: complete web-index discovery is unavailable.
- GDELT fallback is narrow and has not been network-tested.
- Chromium is an optional fallback for JS-heavy approved source URLs,
  NOT unrestricted browsing of all internet sites.
- Real Chromium sandbox may be unavailable under the VPS Docker kernel
  policy. The rollout MUST fail closed rather than use --no-sandbox.
- Sites that disallow robots access or block automated navigation stay HOLD.
- No additional CRM writer, source integration or historical import.
