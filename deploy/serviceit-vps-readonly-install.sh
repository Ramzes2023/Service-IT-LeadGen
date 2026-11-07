#!/usr/bin/env bash
# Opt-in GitHub/manual VPS deployment, NEVER as root, NEVER enabling CRM write.
set -euo pipefail
umask 077

sha="$1"
expected="$2"
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || { echo "BAD_RELEASE_SHA"; exit 2; }
[[ "$expected" =~ ^[a-f0-9]{64}$ ]] || { echo "BAD_ARCHIVE_CHECKSUM"; exit 2; }
[[ "$(id -u)" != "0" ]] || { echo "DO_NOT_DEPLOY_AS_ROOT"; exit 2; }
command -v docker >/dev/null || { echo "DOCKER_NOT_INSTALLED"; exit 2; }
docker compose version >/dev/null || { echo "DOCKER_COMPOSE_MISSING"; exit 2; }

base="$HOME/serviceit"
archive="$base/staging/release-$sha.tgz"
release="$base/releases/$sha"
config="$base/config/vps.env"
[[ -f "$archive" && -f "$config" ]] || { echo "VPS_OWNER_BOOTSTRAP_INCOMPLETE"; exit 2; }
printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --status \
    || { echo "ARCHIVE_CHECKSUM_MISMATCH"; exit 2; }

# Remote SSH deployment is STRICTLY readonly. Live cutover is separate.
grep -qx 'SERVICEIT_VPS_WRITER_MODE=READONLY' "$config" \
    || { echo "READONLY_DEPLOY_ONLY"; exit 2; }
grep -qx 'SERVICEIT_VPS_CUTOVER_AUTHORIZED=NO' "$config" \
    || { echo "CUTOVER_IS_MANUAL_ONLY"; exit 2; }
grep -qx 'SERVICEIT_VPS_INTAKE_ENABLED=NO' "$config" \
    || { echo "EVENT_INTAKE_MUST_START_DISABLED"; exit 2; }

mkdir -p "$base/releases"
if [[ ! -d "$release" ]]; then
    mkdir -m 0700 "$release"
    tar --no-same-owner --no-same-permissions -xzf "$archive" -C "$release"
fi
compose="$release/deploy/serviceit-vps.compose.yml"
[[ -f "$compose" ]] || { echo "COMPOSE_NOT_IN_RELEASE"; exit 2; }
docker compose --env-file "$config" -f "$compose" config --quiet
docker compose --env-file "$config" -f "$compose" build
docker compose --env-file "$config" -f "$compose" run --rm coordinator \
    python -m leadpilot.serviceit_vps_worker --migrate
docker compose --env-file "$config" -f "$compose" up -d --no-build
curl --silent --show-error --fail --max-time 5 \
    http://127.0.0.1:18780/health >/dev/null \
    || { echo "VPS_LOCAL_INGRESS_HEALTH_FAILED"; exit 2; }
echo "SERVICEIT_VPS_READONLY_DEPLOYED_SHA=$sha"
echo "SERVICEIT_CRM_WRITES=0"
echo "NEXT_GATE=TLS_DNS_APP_TOKEN_CLOUD_CUTOVER_HUMAN_APPROVAL"
