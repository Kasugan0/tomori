#!/usr/bin/env bash
set -euo pipefail

# Unique lifecycle and E2E window guard for the tomori (#141) local stack.
# Strictly scoped to tomori containers, volume (tomori_pgdata), and ports.
# Never touches any other project's containers or ports on the host.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/compose.yaml"
ENV_FILE="${ROOT_DIR}/.env"
RUNTIME_DIR="${ROOT_DIR}/.team/runtime"
LOCK_DIR="${RUNTIME_DIR}/e2e.lock"
LOCK_META="${LOCK_DIR}/owner.env"
CONTAINER_NAME="tomori-postgres"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

TOMORI_PG_HOST="${TOMORI_PG_HOST:-127.0.0.1}"
TOMORI_PG_PORT="${TOMORI_PG_PORT:-25432}"
TOMORI_PG_USER="${TOMORI_PG_USER:-tomori}"
TOMORI_PG_PASSWORD="${TOMORI_PG_PASSWORD:-tomori_dev_password}"
TOMORI_PG_DB="${TOMORI_PG_DB:-tomori_dev}"
TOMORI_PG_TEST_DB="${TOMORI_PG_TEST_DB:-tomori_test}"
BACKEND_PORT="${BACKEND_PORT:-18141}"
FRONTEND_PORT="${FRONTEND_PORT:-15173}"
E2E_BACKEND_PORT="${E2E_BACKEND_PORT:-18142}"
E2E_FRONTEND_PORT="${E2E_FRONTEND_PORT:-15174}"

usage() {
  cat <<'EOF'
Usage: ./deploy/stack-guard.sh <command> [options]

Commands:
  up                          Start tomori-postgres and verify tomori_dev & tomori_test readiness
  stop [--force]              Stop tomori-postgres container (preserves tomori_pgdata volume)
  status                      Show tomori container, DB readiness, port status, and E2E lease
  claim-e2e [--owner NAME]    Verify DB & free E2E ports (18142/15174) and acquire E2E lock
  release-e2e [--owner NAME] [--force]
                              Verify E2E ports are free and release E2E lock
EOF
}

require_tools() {
  DOCKER_BIN="$(command -v docker)"
  SS_BIN="$(command -v ss)"
  COMPOSE_VERSION="$("${DOCKER_BIN}" compose version)"
  if [[ -z "${COMPOSE_VERSION}" ]]; then
    echo "[stack-guard] ERROR: docker compose plugin is unavailable" >&2
    exit 1
  fi
}

get_container_row() {
  "${DOCKER_BIN}" ps -a --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}|{{.State}}|{{.Status}}|{{.Ports}}'
}

check_port_free() {
  local port="$1"
  local label="$2"
  local listeners
  listeners="$("${SS_BIN}" -tlnH "sport = :${port}")"
  if [[ -n "${listeners}" ]]; then
    echo "[stack-guard] ERROR: port ${port} (${label}) is already in use:" >&2
    echo "${listeners}" >&2
    return 1
  fi
}

verify_databases() {
  "${DOCKER_BIN}" exec "${CONTAINER_NAME}" pg_isready -h 127.0.0.1 -p 5432 -U "${TOMORI_PG_USER}" -d "${TOMORI_PG_DB}"
  "${DOCKER_BIN}" exec "${CONTAINER_NAME}" pg_isready -h 127.0.0.1 -p 5432 -U "${TOMORI_PG_USER}" -d "${TOMORI_PG_TEST_DB}"
  "${DOCKER_BIN}" exec -e "PGPASSWORD=${TOMORI_PG_PASSWORD}" "${CONTAINER_NAME}" \
    psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 -U "${TOMORI_PG_USER}" -d "${TOMORI_PG_DB}" -tAc \
    "SELECT current_database(), current_user, version();"
  "${DOCKER_BIN}" exec -e "PGPASSWORD=${TOMORI_PG_PASSWORD}" "${CONTAINER_NAME}" \
    psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 -U "${TOMORI_PG_USER}" -d "${TOMORI_PG_TEST_DB}" -tAc \
    "SELECT current_database(), current_user, version();"
}

cmd_up() {
  require_tools
  local init_sql="${ROOT_DIR}/deploy/postgres/init-dbs.sql"
  local init_mode
  init_mode="$(stat -c '%a' "${init_sql}")"
  if [[ "$((8#${init_mode} & 4))" -eq 0 ]]; then
    echo "[stack-guard] ERROR: ${init_sql} (mode ${init_mode}) is not world-readable; container postgres user (uid 70) cannot read it. Run: chmod 0644 deploy/postgres/init-dbs.sql" >&2
    exit 1
  fi

  local row
  row="$(get_container_row)"
  if [[ -z "${row}" ]]; then
    check_port_free "${TOMORI_PG_PORT}" "tomori PostgreSQL"
  else
    local state="${row#*|}"
    state="${state%%|*}"
    if [[ "${state}" != "running" ]]; then
      check_port_free "${TOMORI_PG_PORT}" "tomori PostgreSQL"
    fi
  fi

  "${DOCKER_BIN}" compose -f "${COMPOSE_FILE}" up -d --wait
  verify_databases
  echo "[stack-guard] tomori PostgreSQL is healthy on ${TOMORI_PG_HOST}:${TOMORI_PG_PORT} (dbs: ${TOMORI_PG_DB}, ${TOMORI_PG_TEST_DB})"
}

cmd_stop() {
  require_tools
  local force="false"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --force)
        force="true"
        shift
        ;;
      *)
        echo "[stack-guard] ERROR: unknown argument for stop: $1" >&2
        exit 2
        ;;
    esac
  done

  if [[ -d "${LOCK_DIR}" && "${force}" != "true" ]]; then
    echo "[stack-guard] ERROR: active E2E lease exists at ${LOCK_DIR}; release-e2e first or pass --force:" >&2
    if [[ -f "${LOCK_META}" ]]; then
      cat "${LOCK_META}" >&2
    fi
    exit 1
  fi

  local row
  row="$(get_container_row)"
  if [[ -z "${row}" ]]; then
    echo "[stack-guard] ${CONTAINER_NAME} container does not exist; nothing to stop."
    return 0
  fi

  "${DOCKER_BIN}" compose -f "${COMPOSE_FILE}" stop
  echo "[stack-guard] ${CONTAINER_NAME} stopped (volume tomori_pgdata preserved)."
}

cmd_status() {
  require_tools
  echo "=== Compose Services (${COMPOSE_FILE}) ==="
  "${DOCKER_BIN}" compose -f "${COMPOSE_FILE}" ps -a

  local row
  row="$(get_container_row)"
  echo "=== PostgreSQL Container & Databases ==="
  if [[ -z "${row}" ]]; then
    echo "container: absent (${CONTAINER_NAME})"
  else
    echo "container: ${row}"
    local state="${row#*|}"
    state="${state%%|*}"
    if [[ "${state}" == "running" ]]; then
      "${DOCKER_BIN}" inspect --format 'health={{.State.Health.Status}} image={{.Config.Image}}' "${CONTAINER_NAME}"
      verify_databases
    fi
  fi

  echo "=== Allocated Tomori Ports (ss -tlnH) ==="
  local p label listeners
  for spec in \
    "${TOMORI_PG_PORT}:postgres" \
    "${BACKEND_PORT}:backend-dev" \
    "${FRONTEND_PORT}:frontend-dev" \
    "${E2E_BACKEND_PORT}:backend-e2e" \
    "${E2E_FRONTEND_PORT}:frontend-e2e"; do
    p="${spec%%:*}"
    label="${spec#*:}"
    listeners="$("${SS_BIN}" -tlnH "sport = :${p}")"
    if [[ -n "${listeners}" ]]; then
      echo "port ${p} (${label}): LISTENING -> ${listeners}"
    else
      echo "port ${p} (${label}): FREE"
    fi
  done

  echo "=== E2E Window Lease ==="
  if [[ -d "${LOCK_DIR}" ]]; then
    echo "status: LOCKED (${LOCK_DIR})"
    if [[ -f "${LOCK_META}" ]]; then
      cat "${LOCK_META}"
    fi
  else
    echo "status: FREE"
  fi
}

cmd_claim_e2e() {
  require_tools
  local owner="${TOMORI_E2E_OWNER:-${USER:-unknown}@pid$$}"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --owner)
        if [[ $# -lt 2 ]]; then
          echo "[stack-guard] ERROR: --owner requires a value" >&2
          exit 2
        fi
        owner="$2"
        shift 2
        ;;
      *)
        echo "[stack-guard] ERROR: unknown argument for claim-e2e: $1" >&2
        exit 2
        ;;
    esac
  done

  local row
  row="$(get_container_row)"
  if [[ -z "${row}" ]]; then
    echo "[stack-guard] ERROR: ${CONTAINER_NAME} does not exist. Run ./deploy/stack-guard.sh up first." >&2
    exit 1
  fi
  local state="${row#*|}"
  state="${state%%|*}"
  if [[ "${state}" != "running" ]]; then
    echo "[stack-guard] ERROR: ${CONTAINER_NAME} is not running (${row}). Run ./deploy/stack-guard.sh up first." >&2
    exit 1
  fi

  local health
  health="$("${DOCKER_BIN}" inspect --format '{{.State.Health.Status}}' "${CONTAINER_NAME}")"
  if [[ "${health}" != "healthy" ]]; then
    echo "[stack-guard] ERROR: ${CONTAINER_NAME} health status is '${health}', expected 'healthy'." >&2
    exit 1
  fi

  verify_databases
  check_port_free "${E2E_BACKEND_PORT}" "backend-e2e"
  check_port_free "${E2E_FRONTEND_PORT}" "frontend-e2e"

  mkdir -p "${RUNTIME_DIR}"
  if ! mkdir "${LOCK_DIR}"; then
    echo "[stack-guard] ERROR: E2E window is already claimed (${LOCK_DIR}):" >&2
    if [[ -f "${LOCK_META}" ]]; then
      cat "${LOCK_META}" >&2
    fi
    exit 1
  fi

  local claimed_at
  claimed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  cat > "${LOCK_META}" <<EOF
OWNER=${owner}
CLAIMED_AT_UTC=${claimed_at}
CALLER_PID=$$
TOMORI_PG_HOST=${TOMORI_PG_HOST}
TOMORI_PG_PORT=${TOMORI_PG_PORT}
TOMORI_PG_TEST_DB=${TOMORI_PG_TEST_DB}
E2E_BACKEND_PORT=${E2E_BACKEND_PORT}
E2E_FRONTEND_PORT=${E2E_FRONTEND_PORT}
E2E_DATABASE_URL=postgresql+psycopg://${TOMORI_PG_USER}:${TOMORI_PG_PASSWORD}@${TOMORI_PG_HOST}:${TOMORI_PG_PORT}/${TOMORI_PG_TEST_DB}
EOF

  echo "[stack-guard] E2E window claimed:"
  cat "${LOCK_META}"
}

cmd_release_e2e() {
  require_tools
  local owner=""
  local force="false"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --owner)
        if [[ $# -lt 2 ]]; then
          echo "[stack-guard] ERROR: --owner requires a value" >&2
          exit 2
        fi
        owner="$2"
        shift 2
        ;;
      --force)
        force="true"
        shift
        ;;
      *)
        echo "[stack-guard] ERROR: unknown argument for release-e2e: $1" >&2
        exit 2
        ;;
    esac
  done

  if [[ ! -d "${LOCK_DIR}" ]]; then
    echo "[stack-guard] no active E2E lock at ${LOCK_DIR}."
    check_port_free "${E2E_BACKEND_PORT}" "backend-e2e"
    check_port_free "${E2E_FRONTEND_PORT}" "frontend-e2e"
    return 0
  fi

  if [[ -n "${owner}" && "${force}" != "true" && -f "${LOCK_META}" ]]; then
    local recorded_owner=""
    while IFS='=' read -r k v; do
      if [[ "${k}" == "OWNER" ]]; then
        recorded_owner="${v}"
      fi
    done < "${LOCK_META}"
    if [[ -n "${recorded_owner}" && "${recorded_owner}" != "${owner}" ]]; then
      echo "[stack-guard] ERROR: E2E lock owned by '${recorded_owner}', not '${owner}'. Pass --force to override." >&2
      exit 1
    fi
  fi

  if [[ "${force}" != "true" ]]; then
    check_port_free "${E2E_BACKEND_PORT}" "backend-e2e"
    check_port_free "${E2E_FRONTEND_PORT}" "frontend-e2e"
  fi

  if [[ -f "${LOCK_META}" ]]; then
    rm "${LOCK_META}"
  fi
  rmdir "${LOCK_DIR}"
  echo "[stack-guard] E2E window released (${LOCK_DIR})."
}

main() {
  if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
  fi
  local cmd="$1"
  shift
  case "${cmd}" in
    up)
      cmd_up "$@"
      ;;
    stop)
      cmd_stop "$@"
      ;;
    status)
      cmd_status "$@"
      ;;
    claim-e2e)
      cmd_claim_e2e "$@"
      ;;
    release-e2e)
      cmd_release_e2e "$@"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      echo "[stack-guard] ERROR: unknown command '${cmd}'" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
