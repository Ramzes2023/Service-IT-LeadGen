# Brave Search Web + LLM Context on native VPS (2026-11-08)

## Status / scope

The live Chromium browser is running on the VPS, and the four LIVE native
coordinator/auxiliary/business/observer services are healthy. **Real new-card
nine-field processing remains unverified end-to-end.** Brave is an optional
post-card corporate research tool, NOT a source connector or second CRM writer.

Code already present:
- `leadpilot/serviceit_independent_company_research.py`: up to 3 Brave calls
  per *eligible* new card (2 Web Search + 1 LLM Context) and no extra retry
  on rate-limit errors. Indexed snippets/LLM chunks are discovery candidates,
  never verified corporate identities or direct source evidence.
- `leadpilot/serviceit_vps_worker.py`: reads
  `SERVICEIT_BRAVE_SEARCH_API_KEY` from `..._FILE` inside coordinator;
  empty key triggers existing free GDELT-news-only fallback.

New:
- `deploy/serviceit-vps-brave.compose.yml`: a separate, optional Compose
  overlay mounting only the Brave key in coordinator. No other service sees it.
- `leadpilot/serviceit_vps_brave_auth_probe.py`: one generic Web request and
  one generic Context request; **no** CRM/AI/PG writes and no private data.
- `deploy/serviceit-vps-brave-live-activate.py`: refuses activation unless
  native core and Chromium remain LIVE, checks the secret-only coordinator
  mount, performs generic Brave Web+Context auth probe, then recreates only the
  coordinator. On failure, restore previous coordinator image and Compose.
- `.github/workflows/serviceit-vps-brave-live-activate.yml`: owner-only manual
  SSH deployment of a SHA-verified archive. GitHub merely triggers the
  deployment; API searches run on VPS, not GitHub.

## Prerequisite: securely install the secret on VPS using the existing helper

Never paste the API key into ChatGPT, GitHub issues, scripts, PowerShell command
history, Docker environment, a repository file or CI logs.

1. In Windows PowerShell, open root SSH interactively:
   `ssh -tt -o PreferredAuthentications=password -o PubkeyAuthentication=no root@80.66.79.156`
2. Inspect current Brave status with the *already installed* root helper:
   `python3 /home/serviceitdeploy/serviceit/releases/00988a83767a96318163a58b2b2607f741af0ed4/deploy/serviceit-vps-interactive-secret-install.py inventory-poll-only`
   Look only for `brave_api_key=PRESENT_SECURE`.
3. **If missing**, issue/create a Search API key from
   https://api-dashboard.search.brave.com/ and run:
   `python3 /home/serviceitdeploy/serviceit/releases/00988a83767a96318163a58b2b2607f741af0ed4/deploy/serviceit-vps-interactive-secret-install.py put brave_api_key`
   The root helper asks for the key via a non-echoing TTY prompt and writes
   `/etc/serviceit/secrets/brave_api_key` without overwriting existing files;
   owner UID=10001, mode 0400. Re-run inventory to see `PRESENT_SECURE`.
4. Type `exit` to leave root.

**Owner-only activation workflow:** after this PR is merged and key present,
on Windows PowerShell:
```powershell
$Repo = "Ramzes2023/Service-IT-LeadGen"
gh workflow run serviceit-vps-brave-live-activate.yml --repo $Repo --ref main -f "approval=ACTIVATE_VPS_BRAVE_SEARCH_CONTEXT_1062_21"
gh run list --repo $Repo --workflow serviceit-vps-brave-live-activate.yml --limit 1 --json databaseId,status,conclusion,url
```

Expected logs: `BRAVE_WEB_SEARCH=PASS;BRAVE_LLM_CONTEXT=PASS`,
`BRAVE_VPS_NATIVE_COORDINATOR=LIVE_ENABLED`,
`CORE_FOUR_HEALTHY=PASS;BROWSER_STILL_LIVE=PASS` and
`REAL_NEW_CARD_E2E=NOT_YET_VERIFIED`. The workflow will stop early if the
secret is missing/invalid (potentially after a build). It never creates a
new CRM card, replays old records or enables sources. Only future newly
discovered eligible cards can use Brave research.

## Cost and safety

As checked 2026-11-08, Brave Search plan is $5/1,000 requests and currently
includes $5 monthly credit (about 1,000 calls); vendor may change terms.
300 eligible cards * up to 3 calls = 900 paid-equivalent requests (~$4.50),
PLUS any diagnostic calls (~2 per initial activation) and other shared-key
usage. **There is no global monthly 300-card database quota implemented in
this rollout**: the current code caps calls per card, NOT total cards per month.
If strict no-overage is required, set prepaid amount $0 and review Brave's
dashboard usage/limits, or add an independent atomic request budget before
higher-volume production use. Do not assume a free credit hard-limits spend
without checking the vendor's current billing settings.

The nine AI fields and real CRM card updates have not yet been verified by a
live newly created card. That final step is separate, gated and NOT simulated
by Brave auth testing. No FNS paid EGRUL API and no new lead sources.
