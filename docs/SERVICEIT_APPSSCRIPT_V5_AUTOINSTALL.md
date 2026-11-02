# ServiceIT Apps Script V5: one-command owner deployment

Use **only with the existing owner-controlled script** after a backup. The owner has requested automated Google Apps Script replacement without manually copying/pasting code. Local Windows PowerShell installs an official, SHA-256 pinned portable Node.js only if needed (winget fallback), uses pinned `@google/clasp@3.4.1`, finds exactly one matching existing Service IT / Gmail Tender Auto script, pulls a private backup, preserves unrelated source files, replaces one clear prototype and the manifest, pushes, and pulls again to verify readback.

Important: Google OAuth login still requires the user to approve access in their browser, and Google Apps Script API access may be disabled in their account. No software may legally bypass that consent. The installer **does not** run `checkB2BCenter`, install triggers, enable transmission, modify Bitrix, write CRM, or create a new Apps Script project. It does not establish a deployed 24/7 Gmail → PostgreSQL → Bitrix production pipeline.

Source is read from PR #84 and checked against the exact Git blob SHAs from the previous safe V4 package, avoiding unreviewed code. The app script manifest enables Gmail read-only, external request, and scriptapp scopes; the runtime trigger stays OFF. If the existing script contains unexpected scopes or more than one possible target script file, stop without a Google push. Backup is held in the Windows profile private directory outside Git.

**Convenient bootstrap:** use the exact Git branch and file below with a SHA pin of the committed V5 installer. No need to download another ZIP. Enter this entire block in Windows PowerShell:

```powershell
$Repo = "$env:USERPROFILE\Service_IT_GitHub_Repo\Service-IT-LeadGen"
git -C $Repo fetch origin fix/serviceit-appscript-no-node-safe-handoff-20261102
if ($LASTEXITCODE -ne 0) { throw 'FETCH_FAILED' }
$File = "$env:TEMP\SERVICEIT_GOOGLE_AUTOINSTALL_V5.ps1"
git -C $Repo show "FETCH_HEAD:scripts/SERVICEIT_GOOGLE_AUTOINSTALL_V5.ps1" | Set-Content -LiteralPath $File -Encoding UTF8
if ($LASTEXITCODE -ne 0) { throw 'GET_SCRIPT_FAILED' }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $File
```

Do not claim installation unless output contains `SERVICEIT_V5_GOOGLE_READBACK=PASS`. Stop on any failure; do not blindly repeat a Google write. The script prints a private local diagnostic directory to allow safe troubleshooting without sharing passwords or OAuth tokens.

Not yet deployed: Google Apps Script editing; server HTTPS/HMAC ingress; PostgreSQL canary; Gmail real event; CRM qualified lead path. DRAFT PR #84 remains separate and production must remain OFF.
