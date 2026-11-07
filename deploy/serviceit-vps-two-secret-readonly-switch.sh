#!/usr/bin/env bash
# One-time local switch from existing READONLY docker-run containers to the
# dedicated two-secret READONLY Docker Compose project. Root operator only.
# Does not access or modify Bitrix items and does not enable any LIVE gate.
set -Eeuo pipefail

compose="${1:-/home/serviceitdeploy/serviceit/staging/source/deploy/serviceit-vps-two-secret-readonly.compose.yml}"
old_ingress="serviceit-vps-gateway"
old_coordinator="serviceit-vps-coordinator-readonly"
project="serviceit-leadgen-vps-readonly"

[[ "${2:-}" == "CONFIRM_READONLY_SWITCH" ]] || {
  echo "READONLY_SWITCH_CONFIRMATION_REQUIRED"; exit 2;
}
[[ "$(id -u)" == 0 ]] || { echo "ROOT_REQUIRED_FOR_LOCAL_SWITCH"; exit 2; }
[[ -f "$compose" ]] || { echo "COMPOSE_NOT_FOUND"; exit 2; }

for secret in pg_password bitrix_webhook; do
  [[ -s "/etc/serviceit/secrets/$secret" ]] || {
    echo "REQUIRED_SECRET_MISSING"; exit 2;
  }
done
docker image inspect serviceit-vps:readonly >/dev/null
docker compose -f "$compose" config --quiet

for name in "$old_ingress" "$old_coordinator"; do
  [[ "$(docker inspect --format '{{.State.Running}}' "$name")" == true ]] || {
    echo "OLD_CONTAINER_NOT_RUNNING"; exit 2;
  }
  docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$name" \
    | grep -Fxq 'SERVICEIT_VPS_WRITER_MODE=READONLY' || {
      echo "EXISTING_CONTAINER_NOT_READONLY"; exit 2;
    }
done

[[ -z "$(docker ps -a --filter "label=com.docker.compose.project=$project" --format '{{.Names}}')" ]] || {
  echo "COMPOSE_PROJECT_ALREADY_EXISTS"; exit 2;
}
curl --silent --show-error --fail --max-time 8 \
  http://127.0.0.1:18780/health >/dev/null || {
  echo "OLD_INGRESS_HEALTH_FAILED"; exit 2;
}
echo "READONLY_SWITCH_PREFLIGHT=PASS"

rollback() {
  result=$?
  trap - ERR
  echo "READONLY_SWITCH=ROLLBACK"
  docker compose -f "$compose" down --remove-orphans >/dev/null 2>&1 || true
  docker start "$old_ingress" "$old_coordinator" >/dev/null 2>&1 || true
  echo "ROLLBACK_TO_OLD_DOCKER_RUN=ATTEMPTED"
  exit "$result"
}
trap rollback ERR

docker stop "$old_ingress" "$old_coordinator" >/dev/null
docker compose -f "$compose" up -d --no-build >/dev/null

ready=NO
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl --silent --fail --max-time 4 http://127.0.0.1:18780/health \
       | grep -Fq '"crm_writer_enabled":false'; then
    ready=YES
    break
  fi
  sleep 3
done
[[ "$ready" == YES ]] || { echo "COMPOSE_INGRESS_NOT_READY"; false; }

healthy_coordinator=NO
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  logs="$(docker compose -f "$compose" logs --no-color --tail 30 coordinator)"
  if [[ "$logs" == *READONLY_CRM_FIELD_METADATA_CONFIRMED* ]]; then
    healthy_coordinator=YES
    break
  fi
  sleep 3
done
[[ "$healthy_coordinator" == YES ]] || {
  echo "COMPOSE_READONLY_PREFLIGHT_MISSING"; false;
}

curl --silent --show-error --fail --max-time 12 \
  https://leadgen.serviseit.ru/health | grep -Fq '"crm_writer_enabled":false'

trap - ERR
echo "READONLY_COMPOSE_SWITCH=PASS"
echo "CRM_WRITES=0"
echo "AI_CALLS=0"
echo "OLD_CONTAINERS=STOPPED_PRESERVED_FOR_ROLLBACK"
echo "POLL_AND_EVENT_INTAKE=DISABLED"
