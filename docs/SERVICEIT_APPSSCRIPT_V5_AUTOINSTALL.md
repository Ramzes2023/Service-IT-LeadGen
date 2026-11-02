# Service IT V5 — Windows owner-operated Apps Script automatic installation

**Status 2026-11-02:** A separate reviewed ZIP/PowerShell operator package has been prepared as a conversation artifact; **this PR does not include the V5 executable**. Never fetch or execute a non-existent `scripts/SERVICEIT_GOOGLE_AUTOINSTALL_V5.ps1` path from GitHub.

The ZIP replaces the V4 clipboard-only workflow with a PowerShell 5.1 script that attempts to obtain official Node.js (pinned SHA256, `winget` fallback), runs `@google/clasp@3.4.1`, locates exactly one existing **Service IT — Gmail Tender Automation** Google project, pulls a backup, replaces only the recognized source and approved manifest, pushes and pulls back for verification.

**Requires on the owner's Windows**: verified pre-existing local Service IT Git clone, internet access to GitHub/Node/npm/Google, a working Google Apps Script API, and one-time Google browser OAuth consent if not yet granted. It is impossible to silently bypass Google approval; software must fail closed.

**Explicitly OFF**: no Google time trigger, Gmail forwarding, HMAC server, production Bitrix CRM writes, or lead promotion. `SERVICEIT_V5_GOOGLE_READBACK=PASS` is the success marker only for *code installation*, never for Gmail→PostgreSQL→Bitrix E2E.

The package's Windows execution and actual Google API response have not been verified. If blocked, inspect the `FAILED_PHASE` and `ERROR_CODE` markers only and do not share tokens or logs containing private email data. The separate DRAFT PR #84 remains the implementation branch for independent Gmail ingress/server work.
