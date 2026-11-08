# One-time ServiceIT Chromium-only AppArmor userns allowance (Ubuntu 24.04)

## Why
Ubuntu 24.04 restricted user namespaces. Real VPS tests show:
- Chromium with sandbox and Docker default AppArmor: FAILED.
- Chromium with official Playwright seccomp alone: FAILED.
- kernel.apparmor_restrict_unprivileged_userns: ON.
- Root access has been confirmed.

**Do NOT** set the kernel AppArmor sysctl to 0, use AppArmor unconfined,
disable Chromium sandbox, restart the Docker daemon or modify four LIVE workers.

## What is changed
This PR includes deploy/serviceit-chromium.apparmor derived from the Moby
Docker default AppArmor profile plus one extra rule, userns,. It keeps
the Docker-style deny list for mounts, /proc, /sys, unsafe socket families,
and enforces the named AppArmor profile. Only the separate browser container
selects it through Docker Compose security_opt.
The unprivileged egress proxy and four native LIVE workers remain unchanged.

## One-time manual host install (only on owner-approved root session)
From the owner's Windows PowerShell with GitHub CLI already authenticated:

    $Repo = "Ramzes2023/Service-IT-LeadGen"
    $Local = Join-Path $env:TEMP "serviceit-chromium.apparmor"
    $Meta = gh api "repos/$Repo/contents/deploy/serviceit-chromium.apparmor" | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $Meta.sha -ne "c51bdc970632bdc94354b87f8d5b197fcd7ff287") { throw "APPARMOR_SOURCE_UNVERIFIED" }
    [System.IO.File]::WriteAllBytes($Local, [Convert]::FromBase64String(($Meta.content -replace "\\s", "")))
    $Digest = (Get-FileHash -LiteralPath $Local -Algorithm SHA256).Hash.ToLower()
    scp $Local root@80.66.79.156:/root/serviceit-chromium.apparmor
    if ($LASTEXITCODE -ne 0) { throw "APPARMOR_COPY_FAILED" }
    ssh -tt root@80.66.79.156 "echo '$Digest  /root/serviceit-chromium.apparmor' | sha256sum -c - && apparmor_parser -Q /root/serviceit-chromium.apparmor && install -m 0644 -o root -g root /root/serviceit-chromium.apparmor /etc/apparmor.d/serviceit-chromium && apparmor_parser -r /etc/apparmor.d/serviceit-chromium && grep -F 'serviceit-chromium (enforce)' /sys/kernel/security/apparmor/profiles"

Note: Root will ask for password on SCP and SSH separately. Do not disclose it.
Any parser or permission failure MUST STOP before the Chromium sandbox probe.

## Verify without cloud, CRM, or external sites
After installation, run the owner-only GitHub workflow:
.github/workflows/serviceit-vps-browser-offline-sandbox-probe.yml
with approval PROBE_CHROMIUM_SANDBOX_OFFLINE_NO_CRM.

Required success:
CHROMIUM_SANDBOX_WITH_SCOPED_SECCOMP_APPARMOR=PASS
CORE_LIVE_UNCHANGED=YES
CRM_WRITES=0
BROWSER_URLS_VISITED=0

If it fails, inspect the AppArmor audit event and profile status.
Do not start the optional browser sidecar or flip any writer flag.
If it passes, review the separate guarded LIVE browser activation workflow.

## Recovery
On failure after profile installation, no core container needs rollback
because the profile is not selected by four LIVE services.
Do not remove the profile while an attached browser container is running.
For actual removal, first stop browser sidecars and then:
apparmor_parser -R /etc/apparmor.d/serviceit-chromium
rm /etc/apparmor.d/serviceit-chromium

Passwords previously shared in chat should be rotated afterward.
