#!/usr/bin/env bash
# Run via GitHub Actions over a pinned SSH host key.
# This script makes NO filesystem, DB, firewall, Caddy or Bitrix changes.
set -euo pipefail
set +x

echo "SERVICEIT_VPS_SSH_AUTH=PASS"
echo "SERVICEIT_VPS_MODE=DIAGNOSTIC_READONLY"
echo "CRM_WRITES=0"
echo "PG_WRITES=0"
if [[ "$(id -u)" -eq 0 ]]; then
  echo "REMOTE_LOGIN=ROOT_AUTHENTICATED"
else
  echo "REMOTE_LOGIN=NONROOT"
fi

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  printf 'LINUX_DISTRIBUTION=%s\n' "$ID"
  printf 'LINUX_VERSION_ID=%s\n' "$VERSION_ID"
fi
printf 'ARCHITECTURE=%s\n' "$(uname -m)"
printf 'CPU_COUNT=%s\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)"
if [[ -r /proc/meminfo ]]; then
  awk '/^MemTotal:/{printf "RAM_KB=%s\n", $2;exit}' /proc/meminfo
fi
if command -v docker >/dev/null 2>&1; then
  echo "DOCKER_ENGINE=INSTALLED"
  if docker compose version >/dev/null 2>&1; then
    echo "DOCKER_COMPOSE=INSTALLED"
  else
    echo "DOCKER_COMPOSE=MISSING"
  fi
else
  echo "DOCKER_ENGINE=MISSING"
  echo "DOCKER_COMPOSE=MISSING"
fi
if command -v caddy >/dev/null 2>&1; then
  echo "CADDY_BINARY=PRESENT"
  if systemctl is-active --quiet caddy 2>/dev/null; then
    echo "CADDY_SERVICE=ACTIVE"
  else
    echo "CADDY_SERVICE=NOT_ACTIVE_OR_UNAVAILABLE"
  fi
else
  echo "CADDY_BINARY=MISSING"
fi
if command -v pg_isready >/dev/null 2>&1; then
  if pg_isready -q -h 127.0.0.1 -p 5432 -d leadgen -U leadgen; then
    echo "POSTGRES_LOCAL_PORT=ACCEPTING"
  else
    echo "POSTGRES_LOCAL_PORT=NOT_ACCEPTING"
  fi
else
  # Bash /dev/tcp verifies socket presence without submitting credentials.
  if timeout 3 bash -c 'true >/dev/tcp/127.0.0.1/5432' 2>/dev/null; then
    echo "POSTGRES_LOCAL_PORT=TCP_OPEN"
  else
    echo "POSTGRES_LOCAL_PORT=UNREACHABLE"
  fi
fi
if command -v getent >/dev/null 2>&1; then
  host_ip="$(getent ahostsv4 leadgen.serviseit.ru 2>/dev/null | awk 'NR==1{print $1}' || true)"
  if [[ "$host_ip" == "80.66.79.156" ]]; then
    echo "DOMAIN_A_RECORD=EXPECTED_VPS_IP"
  else
    echo "DOMAIN_A_RECORD=NOT_VERIFIED"
  fi
fi
echo "SSH_DIAGNOSTIC_COMPLETE=YES"
