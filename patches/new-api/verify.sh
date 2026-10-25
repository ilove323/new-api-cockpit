#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Download only the pinned public source, never use site data or project secrets.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
commit=e835b1bfc43b565d625537830dbfb19328925134
[[ $# -eq 0 || ( $# -eq 1 && "$1" = --databases ) ]] || { echo 'Usage: verify.sh [--databases]' >&2; exit 2; }
work=$(mktemp -d "${TMPDIR:-/tmp}/cockpit-new-api-patch.XXXXXXXX")
cleanup() {
  local result=$?
  trap - EXIT
  # This entire directory was created above by this invocation.
  if [[ "$work" = */cockpit-new-api-patch.* ]]; then
    chmod -R u+w "$work"
    find "$work" -depth -delete
  fi
  exit "$result"
}
trap cleanup EXIT
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  "https://codeload.github.com/QuantumNous/new-api/tar.gz/$commit" -o "$work/source.tgz"
tar -xzf "$work/source.tgz" -C "$work"
cd "$work/new-api-$commit"
git apply --check --whitespace=error "$here/token-config.patch"
git apply --whitespace=error "$here/token-config.patch"
export GOMAXPROCS=${GOMAXPROCS:-2}
go test -p 2 ./model ./controller ./middleware ./router \
  -run 'TokenConfigPatch|AccessToken.*(Route|Scope)' -count=1
if [[ ${1:-} = --databases ]]; then
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go test -p 2 -c -o "$work/model.test" ./model
  "$here/test-databases.sh" "$work/model.test"
fi
