# Chromium sandbox sys_chroot failure: scoped seccomp delta

The 2026-11-08 owner-run offline Chromium probe
(https://github.com/Ramzes2023/Service-IT-LeadGen/actions/runs/42517652764)
failed. A separate, safe, no-network diagnostic from the owner identified:

    Check failed: sys_chroot("/proc/self/fdinfo/") == 0
    Zygote process exited prematurely

This is the Chromium zygote chroot sandbox. Chromium's own source performs
this call inside a newly created user namespace. On our VPS the enforcing
`serviceit-chromium` AppArmor profile and `unshare -Ur` have both been
verified, so we must not disable the Chromium sandbox or AppArmor.

The Microsoft Playwright pinned seccomp template allows `chroot` only
conditional on CAP_SYS_CHROOT in the **outer** container. We deliberately
run the browser with all outer capabilities dropped (`cap_drop: [ALL]`).
That combination can cause the seccomp filter to deny chroot even though
the sandboxed browser created its own user namespace, where the kernel
enforces capabilities anew.

The new file `deploy/serviceit-chromium-sandbox-seccomp.json` is the exact
pinned Playwright profile plus **one** additional `SCMP_ACT_ALLOW` rule
for the `chroot` syscall. This is permission in the syscall filter, **not**
a capability grant. The kernel's capability checks still apply. We
continue to drop ALL outer Linux capabilities, enable Chromium sandbox,
enforce the dedicated AppArmor profile, use a read-only filesystem with
RAM-only /tmp and isolate the browser from direct public network access.
The egress proxy and four native LIVE workers do not receive this policy.

## Validation

- CI: schema and exact one-rule delta tests.
- VPS: owner-only, **offline** workflow
  `.github/workflows/serviceit-vps-browser-offline-sandbox-probe.yml`;
  approval `PROBE_CHROMIUM_SANDBOX_OFFLINE_NO_CRM`.
- Required: `CHROMIUM_SANDBOX_WITH_SCOPED_SECCOMP_APPARMOR=PASS`.
- The workflow does not reach websites, update Bitrix cards or
  recreate LIVE workers.

If this still fails, take a sanitized, read-only AppArmor audit and
Chromium startup trace; do NOT broaden privileged capabilities or use
`--no-sandbox`.

Only after sandbox success should the separate owner-approved LIVE
browser activation be considered.
