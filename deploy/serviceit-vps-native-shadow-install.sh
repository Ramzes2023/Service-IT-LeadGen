#!/usr/bin/env bash
# Safe optional native shadow deploy: NO live CRM writes, NO cutover.
# Existing old READONLY gateway keeps port 18780 and runs untouched.
set -euo pipefail
umask 077
[[ "$#" -eq 2 ]] || { echo "SHADOW_RELEASE_ARGS_REQUIRED"; exit 2; }

sha="$1"
expected="$2"
[[ "$sha" =~ ^[a-f0-9]{40}$ && "$expected" =~ ^[a-f0-9]{64}$ ]] \
  || { echo "SHADOW_INVALID_RELEASE_ID"; exit 2; }
[[ "$(id -u)" != 0 ]] || { echo "SHADOW_DEPLOY_NONROOT_REQUIRED"; exit 2; }

base="$HOME/serviceit"
archive="$base/staging/release-$sha.tgz"
release="$base/releases/$sha"
config="$base/config/vps.env"
[[ -f "$archive" && -f "$config" ]] \
  || { echo "SHADOW_BOOTSTRAP_MISSING"; exit 2; }
printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --status \
  || { echo "SHADOW_ARCHIVE_HASH_INVALID"; exit 2; }

for must in \
  "SERVICEIT_VPS_WRITER_MODE=READONLY" \
  "SERVICEIT_VPS_CUTOVER_AUTHORIZED=NO" \
  "SERVICEIT_VPS_POLL_ENABLED=NO" \
  "SERVICEIT_VPS_INTAKE_ENABLED=NO"; do
    grep -Fqx "$must" "$config" \
      || { echo "SHADOW_REQUIRED_READONLY_GATES_MISSING"; exit 2; }
done

for variable in \
  SERVICEIT_VPS_OWN_STOP_ENABLED \
  SERVICEIT_VPS_CRM_DUPLICATE_ENABLED \
  SERVICEIT_VPS_KANBAN_TITLE_ENABLED \
  SERVICEIT_VPS_MANAGER_TASK_ENABLED \
  SERVICEIT_VPS_FNS_CRM_RESULT_ENABLED \
  SERVICEIT_VPS_PROVISIONAL_SCORE_ENABLED \
  SERVICEIT_VPS_FREE_MSP_SCORE_ENABLED \
  SERVICEIT_VPS_POSTCALL_ENABLED \
  SERVICEIT_VPS_FINANCE_ENABLED \
  SERVICEIT_VPS_FNS_MSP_ENABLED; do
    # The flag may be unset (default NO), but must never be YES.
    if grep -Eq "^$variable=" "$config" \
       && ! grep -Fqx "$variable=NO" "$config"; then
       echo "SHADOW_AUX_WRITER_FLAG_MUST_BE_OFF"
       exit 2
    fi
done

command -v docker >/dev/null \
  || { echo "SHADOW_DOCKER_REQUIRED"; exit 2; }
docker compose version >/dev/null \
  || { echo "SHADOW_DOCKER_COMPOSE_REQUIRED"; exit 2; }

mkdir -p "$base/releases"
if [[ ! -d "$release" ]]; then
    mkdir -m 0700 "$release"
    tar --no-same-owner --no-same-permissions -xzf "$archive" -C "$release"
fi
compose="$release/deploy/serviceit-vps.compose.yml"
[[ -f "$compose" ]] || { echo "SHADOW_COMPOSE_MISSING"; exit 2; }

# Different Compose project name; never start the competing ingress.
docker compose --env-file "$config" -f "$compose" config --quiet
docker compose --env-file "$config" -f "$compose" build \
    coordinator auxiliaries business observer
# Fail early if the hardened non-root image cannot read app modules;
# no database/CRM/network access during this explicit smoke test.
# Never let docker compose run consume stdin from an SSH "bash -s" script.
# Both standalone invocations explicitly disable interactive stdin and TTY.
docker compose --env-file "$config" -f "$compose" run -T \
    --rm --no-deps coordinator python -c \
    "import os; assert os.geteuid()==10001; import leadpilot.serviceit_vps_worker, leadpilot.serviceit_vps_native_aux, leadpilot.serviceit_vps_native_business, leadpilot.serviceit_vps_native_observer; print('SHADOW_NONROOT_MODULE_IMPORT=PASS')" \
    </dev/null

docker compose --env-file "$config" -f "$compose" run -T \
    --rm --no-deps coordinator python -m leadpilot.serviceit_vps_worker --migrate \
    </dev/null

echo "SHADOW_DB_SCHEMA_STEP_FINISHED=YES"

docker compose --env-file "$config" -f "$compose" up \
    -d --no-build --no-deps coordinator auxiliaries business observer

# Do not declare SUCCESS merely because docker compose up returned 0.
# Inspect EVERY expected service after start, including healthchecks, and
# exit nonzero if a process is absent, restarting, exited or unhealthy.
expected_services=(coordinator auxiliaries business observer)
healthy=0
for attempt in $(seq 1 18); do
    healthy=1
    for service in "${expected_services[@]}"; do
        container_id="$(docker compose --env-file "$config" -f "$compose" ps -aq "$service")"
        if [[ ! "$container_id" =~ ^[a-f0-9]{12,64}$ ]]; then
            healthy=0
            break
        fi
        state="$(docker inspect --format '{{.State.Running}}:{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$container_id")"
        if [[ "$state" != 'true:healthy' ]]; then
            healthy=0
            break
        fi
    done
    [[ "$healthy" -eq 1 ]] && break
    sleep 5
done
if [[ "$healthy" -ne 1 ]]; then
    echo "SHADOW_EXPECTED_FOUR_HEALTHY_SERVICES_NOT_CONFIRMED"
    docker compose --env-file "$config" -f "$compose" ps -a
    exit 2
fi
echo "SHADOW_FOUR_HEALTHY_SERVICES=PASS"

echo "SERVICEIT_VPS_NATIVE_SHADOW_DEPLOYED_SHA=$sha"
echo "SERVICEIT_VPS_SHADOW_WRITES=DISABLED"
echo "SERVICEIT_GITHUB_CLOUD_WRITERS=UNCHANGED"
echo "SERVICEIT_EXISTING_READONLY_GATEWAY=UNCHANGED"
