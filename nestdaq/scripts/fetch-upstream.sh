#!/usr/bin/env bash
# Print, and only optionally execute, NestDAQ source fetch.
# Default is dry-run. Clone requires NESTDAQ_FETCH_APPROVED=yes.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
manifest="${repository_root}/manifests/upstream.yaml"

pinned() { awk -v key="  $1:" '$0 == key {f=1} f && /commit:/ {print $2; exit}' "${manifest}"; }
nestdaq_commit="$(pinned nestdaq)"
user_impl_commit="$(pinned nestdaq-user-impl)"
uhbook_commit="$(pinned uhbook)"
dest="${NESTDDAQ_FETCH_DIR:-${repository_root}/../nestdaq-src}"

usage() {
  cat <<EOF
Usage: $0 [--execute]

Default: print clone commands for the pinned commits and exit 0.
--execute: clone only if NESTDAQ_FETCH_APPROVED=yes.

Destination defaults to a sibling directory (not inside Git):
  ${dest}

Pinned commits:
  nestdaq            ${nestdaq_commit}
  nestdaq-user-impl  ${user_impl_commit}
  uhbook             ${uhbook_commit}
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

commands=$(cat <<EOF
mkdir -p "${dest}"
git clone https://github.com/spadi-alliance/nestdaq.git "${dest}/nestdaq"
git -C "${dest}/nestdaq" checkout ${nestdaq_commit}
git clone https://github.com/spadi-alliance/nestdaq-user-impl.git "${dest}/nestdaq-user-impl"
git -C "${dest}/nestdaq-user-impl" checkout ${user_impl_commit}
git clone https://github.com/spadi-alliance/uhbook.git "${dest}/uhbook"
git -C "${dest}/uhbook" checkout ${uhbook_commit}
EOF
)

echo "dry-run clone plan (no credentials, no production network settings):"
echo "${commands}"

if [[ "${1:-}" != "--execute" ]]; then
  exit 0
fi

if [[ "${NESTDAQ_FETCH_APPROVED:-}" != "yes" ]]; then
  echo "refusing --execute: set NESTDAQ_FETCH_APPROVED=yes to confirm the clone" >&2
  exit 2
fi

echo "cloning into ${dest}"
eval "${commands}"
