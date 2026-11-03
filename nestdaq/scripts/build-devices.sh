#!/usr/bin/env bash
# Build the E88 FairMQ devices in nestdaq/devices against the NestDAQ prefix.
#
# No board access. Build output goes outside the source tree (default: build/devices).
# Requires NESTDDAQ_PREFIX, normally from config/local/nestdaq.env.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repository_root}"

if [[ -z "${NESTDDAQ_PREFIX:-}" && -f config/local/nestdaq.env ]]; then
  set -a
  # shellcheck disable=SC1091
  . config/local/nestdaq.env
  set +a
fi

if [[ -z "${NESTDDAQ_PREFIX:-}" || ! -d "${NESTDDAQ_PREFIX}" ]]; then
  echo "#E: NESTDDAQ_PREFIX is unset or missing; cannot build the devices" >&2
  exit 4
fi

build_dir="${1:-${repository_root}/build/devices}"
mkdir -p "${build_dir}"

cmake -S nestdaq/devices -B "${build_dir}" \
  -DCMAKE_PREFIX_PATH="${NESTDDAQ_PREFIX}" \
  -DBOOST_ROOT="${NESTDDAQ_PREFIX}"
cmake --build "${build_dir}" --parallel

echo "#D: built:"
for device in StfPartSender; do
  printf '  %s\n' "${build_dir}/${device}"
done
