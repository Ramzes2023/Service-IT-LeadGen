# TinyFish free Search/Fetch: V21 safe server secret preparation

**This is a configuration-only staging action. It does not install the V21
Python source in the currently running VPS image, does not restart Docker and
does not enable paid or free provider calls.**

The owner has provisioned an independent server API key as
`/etc/serviceit/secrets/tinyfish_api_key`, chmod 0400, uid/gid 10001,
44 bytes. The key itself is never stored or transmitted through GitHub.
The existing Docker coordinator remains healthy, with 4 existing secrets.

## Docker Compose overlay

`deploy/serviceit-vps-tinyfish-free.compose.yml` appends the *fifth*
read-only secret solely to coordinator. It adds four environment variables:
the mounted *_FILE pathname and three explicit `NO` flags. All existing
Bitrix, Brave, RouterAI and PostgreSQL secrets are preserved.

Docker Compose merges existing secrets by destination target: see official
documentation https://docs.docker.com/reference/compose-file/merge/.

The named secret is file-backed by
`${SERVICEIT_VPS_SECRETS_DIR}/tinyfish_api_key`. Do not store key bytes
as an environment variable or enter them in ChatGPT.

## Owner-only OFF-stage workflow

On Issue #368, owner posts the exact comment:

    SERVICEIT_B2B_TINYFISH_COMPOSE_STAGE_368

The owner-only workflow transfers exactly two Git-attested files via already
configured SSH deploy keys. The remote Python stage controller verifies:

* Live **healthy** coordinator, verified Docker image identity, and the three
  actual existing Compose config paths (base, Brave, immutable image pin).
* There is no concurrent locked coordinator deployment.
* Production `vps.env` remains unchanged.
* The effective `docker compose config --format json` before/after differs
  by only the fifth coordinator secret and four explicitly `OFF` env vars.
  All other service configs, networks, images, command, secrets and health
  settings must be byte-identical after normalized JSON comparison.
* Stage location is `~/serviceit/config/serviceit-vps-tinyfish-free.compose.yml`
  as the non-root deploy user, mode 0600.
* Current Docker image and Compose labels remain identical after staging.

The workflow deliberately never executes `docker compose up`, `restart`,
`down`, `docker run`, API calls, PostgreSQL queries or CRM actions.

Success prints only:

    TINYFISH_STAGE_RESULT=CONFIG_ONLY_STAGED
    TINYFISH_STAGE_COMPOSE_PREVIEW=PASS
    TINYFISH_STAGE_COORDINATOR_RESTARTED=NO
    TINYFISH_STAGE_CRM_WRITES=NO
    TINYFISH_STAGE_FETCH_ENABLED=NO

A stage PASS does **not** mean the key is visible within the *currently*
running container: actual Docker Compose apply is a separately reviewed,
fully guarded release with a return-to-old-image rollback, source hash
checks, in-flight PG receipt fence and approved provider / website terms.

## Next independent release gates

1. Verify current live coordinator image SHA and V22/V20 module SHA against
   the latest main; parallel project work may have updated the image.
2. Build and smoke-test the V21 Python source changes *on the current live
   image*, offline, with no old-code downgrade.
3. Review exact owner-approved source licensing. The B2B-Center page is
   inaccessible for automated extraction without written platform consent.
4. After approval, install the fifth Compose secret with all provider
   feature flags STILL `NO`; verify the new image, other services health and
   exact container mount.
5. Set one allowed public-source opt-in at a time, within provider free
   limits, with V19/V20 receipts/lock and exact Bitrix readback.
