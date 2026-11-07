# VPS: install missing secrets safely (local operator only)

STATUS 2026-11-07: two-secrets READONLY Compose is healthy. There are exactly
seven missing secret FILES. Nothing has been imported from GitHub Actions.

## Key facts

- GitHub repository Secrets are WRITE-ONLY: their saved values cannot be
  read back from GitHub's UI or GitHub's REST API.
- Existing Google/GigaChat/RouterAI/Brave credentials must be obtained from
  the original issuer or rotated there. Rotating a credential used by the
  existing GitHub workflows requires updating that GitHub Secret in the same
  change window; never break the production workflow silently.
- SERVICEIT_BITRIX_EVENT_BRIDGE_KEY and SERVICEIT_GH_LEDGER_KEY are shared
  signing keys. Do not generate random replacements only on the VPS: they
  MUST be equal to the keys of the old cloud workers at cutover. If their
  original values cannot be retrieved, plan an explicitly coordinated
  key rotation, with old writers disabled and validation before LIVE.
- bitrix_app_token must be the genuine app token from the Bitrix event
  binding, not the existing REST webhook.
- github_ledger_pat is a separate long-lived GitHub credential, not the
  short-lived GITHUB_TOKEN automatically provisioned in Actions. Use a
  scoped fine-grained token for the exact repository and only the permissions
  needed to read and write the encrypted ledger (repository Contents).
- Never expose any value to ChatGPT, the GitHub repository, workflow logs,
  environment variables, command-line parameters, or shell history.

## On VPS: check metadata (no secret content)

Run in interactive Ubuntu root shell, after git pull of the merged PR:

```bash
APP=/home/serviceitdeploy/serviceit/staging/source
python3 "$APP/deploy/serviceit-vps-interactive-secret-install.py" inventory
```

## When one actual valid credential is in hand

Example for RouterAI (one line, not sent through this chat):

```bash
APP=/home/serviceitdeploy/serviceit/staging/source
python3 "$APP/deploy/serviceit-vps-interactive-secret-install.py" put routerai_key
```

The program asks for the value with input echo turned OFF and creates
/etc/serviceit/secrets/routerai_key owned by uid/gid 10001 with mode 0400.
It does not overwrite existing values. It checks /etc/serviceit/secrets is
root-owned, mode 0700, and refuses noninteractive sessions. It does not
restart containers, change LIVE flags, or call Bitrix.

Repeat one at a time for: bitrix_app_token, event_bridge_key, gh_ledger_key,
github_ledger_pat, brave_api_key, routerai_key, gigachat_credentials.
Only install genuine correct values; skip unknown ones. GitHub-housed keys
cannot be recovered just by looking at GitHub Settings.

After each insertion, verify with inventory. If the provider key was
rotated, coordinate updating the existing GitHub Actions Secret and
validate the old worker before any cutover.

No reading or printing of secret file contents and no `cat`, `echo value`,
`export KEY=...`, or command-line token parameters.

## Current operational state

The main two-secret READONLY Compose continues to run without any of these
files. Do NOT start the full nine-secret Compose or authorize CRM writing
until the set is complete, external issuers and legacy workers agree on
shared keys, and separate end-to-end verification passes.

Note: credentials and SSH private keys previously exposed in chats must
be rotated and access reviewed before LIVE cutover.
