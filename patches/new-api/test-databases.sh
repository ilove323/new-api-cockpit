#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Run only the fixed Unix-socket fixtures embedded in the patched Go test.
set -euo pipefail

binary=${1:?Usage: test-databases.sh /absolute/path/to/linux-amd64-model.test}
[[ "$binary" = /* && -f "$binary" && -x "$binary" ]] || { echo 'Expected an executable absolute test-binary path.' >&2; exit 2; }
prefix="cockpit-token-config-$(date +%s)-$$"
work=$(mktemp -d "${TMPDIR:-/tmp}/cockpit-token-config-results.XXXXXXXX")
containers=()
volumes=()
pulled_images=()
existing=()
while IFS= read -r id; do [[ -z "$id" ]] || existing+=("$id"); done < <(docker ps -aq)
snapshot() {
  if ((${#existing[@]})); then
    docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.RestartCount}} {{.State.Status}}' "${existing[@]}" | sort
  fi
}
snapshot > "$work/before"

cleanup() {
  local result=$?
  trap - EXIT INT TERM
  for name in "${containers[@]}"; do
    if [[ "$name" = "$prefix"-* ]]; then
      if ((result)); then docker logs --tail 12 "$name" >&2 2>/dev/null || true; fi
      docker rm -fv "$name" >/dev/null 2>&1 || true
    fi
  done
  for name in "${volumes[@]}"; do
    [[ "$name" != "$prefix"-* ]] || docker volume rm "$name" >/dev/null 2>&1 || true
  done
  for name in "${pulled_images[@]}"; do
    # Docker refuses to remove an image still used by another container.
    docker image rm "$name" >/dev/null 2>&1 || true
  done
  if ! snapshot > "$work/after" || ! cmp -s "$work/before" "$work/after"; then
    echo 'Existing container metadata changed during verification; inspect before drawing deployment conclusions.' >&2
    result=1
  else
    echo 'Existing containers: IDs, start times, restart counts and states unchanged.'
  fi
  if docker ps -a --format '{{.Names}}' | grep -q "^${prefix}-" || docker volume ls --format '{{.Name}}' | grep -q "^${prefix}-"; then
    echo "Fixture cleanup incomplete: ${prefix}" >&2
    result=1
  fi
  # Only files created by this invocation, never project or server data.
  [[ "$work" = */cockpit-token-config-results.* ]] && find "$work" -type f -delete && rmdir "$work"
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ensure_image() {
  if ! docker image inspect "$1" >/dev/null 2>&1; then
    docker pull "$1"
    pulled_images+=("$1")
  fi
  docker image inspect --format 'Fixture image: {{.Id}}' "$1"
}
new_volume() {
  volumes+=("$1")
  docker volume create "$1" >/dev/null
}

postgres_image=${POSTGRES_TEST_IMAGE:-postgres:16-alpine}
redis_image=${REDIS_TEST_IMAGE:-redis:7-alpine}
mysql_image=${MYSQL_TEST_IMAGE:-mysql:8.4}
ensure_image "$redis_image"

for dialect in postgres mysql; do
  redis="$prefix-$dialect-redis"
  server="$prefix-$dialect-db"
  runner="$prefix-$dialect-runner"
  redis_volume="$redis-socket"
  socket_volume="$server-socket"
  data_volume="$server-data"
  new_volume "$redis_volume"
  new_volume "$socket_volume"
  new_volume "$data_volume"
  containers+=("$redis" "$server" "$runner")
  docker run -d --name "$redis" --network none --cpus 0.25 --memory 64m --memory-swap 64m --pids-limit 64 \
    --read-only --cap-drop ALL --security-opt no-new-privileges --user 999:1000 \
    --mount "type=volume,source=$redis_volume,target=/data" "$redis_image" \
    redis-server --port 0 --unixsocket /data/redis.sock --unixsocketperm 777 --save '' --appendonly no >/dev/null
  mounts=(--mount "type=volume,source=$redis_volume,target=/fixture/redis,readonly")
  if [[ "$dialect" = postgres ]]; then
    ensure_image "$postgres_image"
    docker run -d --name "$server" --network none --cpus 1 --memory 512m --memory-swap 512m --pids-limit 128 \
      --mount "type=volume,source=$socket_volume,target=/var/run/postgresql" \
      --mount "type=volume,source=$data_volume,target=/var/lib/postgresql/data" \
      -e POSTGRES_DB=cockpit_token_config_fixture -e POSTGRES_USER=fixture_admin -e POSTGRES_HOST_AUTH_METHOD=trust \
      "$postgres_image" postgres -c listen_addresses='' -c max_connections=10 -c shared_buffers=32MB >/dev/null
    mounts+=(--mount "type=volume,source=$socket_volume,target=/fixture/pg,readonly")
  else
    ensure_image "$mysql_image"
    docker run -d --name "$server" --network none --cpus 1 --memory 768m --memory-swap 768m --pids-limit 256 \
      --mount "type=volume,source=$socket_volume,target=/var/run/mysqld" \
      --mount "type=volume,source=$data_volume,target=/var/lib/mysql" \
      -e MYSQL_ALLOW_EMPTY_PASSWORD=yes -e MYSQL_DATABASE=cockpit_token_config_fixture \
      -e MYSQL_USER=fixture_admin -e MYSQL_PASSWORD=fixture-only-not-real \
      "$mysql_image" --socket=/var/run/mysqld/mysql.sock --skip-networking --mysqlx=OFF --max-connections=10 --innodb-buffer-pool-size=64M --performance-schema=OFF >/dev/null
    mounts+=(--mount "type=volume,source=$socket_volume,target=/fixture/mysql,readonly")
  fi
  ready=false
  for ((attempt=0; attempt<90; attempt++)); do
    if [[ "$dialect" = postgres ]]; then
      if docker exec "$server" pg_isready -U fixture_admin -d cockpit_token_config_fixture >/dev/null 2>&1; then ready=true; break; fi
    elif docker exec "$server" sh -c 'test "$(cat /proc/1/comm)" = mysqld' >/dev/null 2>&1 && \
      docker exec -e MYSQL_PWD=fixture-only-not-real "$server" mysql --protocol=socket --socket=/var/run/mysqld/mysql.sock -u fixture_admin \
      -D cockpit_token_config_fixture -Nse 'SELECT 1' >/dev/null 2>&1; then ready=true; break
    fi
    sleep 1
  done
  [[ "$ready" = true ]] || { echo "$dialect fixture did not become ready." >&2; exit 1; }
  docker exec "$redis" redis-cli -s /data/redis.sock ping >/dev/null
  docker run --name "$runner" --network none --cpus 1 --memory 512m --memory-swap 512m --pids-limit 128 \
    --read-only --cap-drop ALL --security-opt no-new-privileges --user 65532:65532 \
    --tmpfs /tmp:rw,nosuid,nodev,size=256m --mount "type=bind,source=$binary,target=/fixture/model.test,readonly" \
    "${mounts[@]}" -e "COCKPIT_NEWAPI_TEST_DATABASE=$dialect" "$redis_image" \
    /fixture/model.test -test.run '^TestTokenConfigPatchDatabaseConcurrency$' -test.v -test.timeout 45s
  docker rm -fv "$runner" "$server" "$redis" >/dev/null
done
