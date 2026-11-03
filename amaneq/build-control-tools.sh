#!/usr/bin/env bash
# Build the board control tools at pinned commits:
#   hul-common-lib  read_register, write_register, set_hbfstate, ...
#   amaneq-soft     Str-HRTDC tools, including the DDR/LUT initialization used by
#                   "run-control/daqctl.py prepare"
# and print their SHA-256 so that a run report can name the exact binaries.
#
# Host side only: nothing here talks to a board. Sources, build and install trees
# go under <work-dir>, outside this repository.
#
# Usage: build-control-tools.sh <work-dir>
#   result: <work-dir>/hul-common-lib/install/bin/*, <work-dir>/amaneq-soft/install/bin/*
# Needs: git, cmake, make, a C++17 compiler; network access to GitHub for the first clone.
set -euo pipefail

manifest="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/manifests/upstream.yaml"
pinned() { awk -v key="  $1:" '$0 == key {f=1} f && /commit:/ {print $2; exit}' "${manifest}"; }
url() { awk -v key="  $1:" '$0 == key {f=1} f && /url:/ {print $2; exit}' "${manifest}"; }

work_dir="${1:?usage: $0 <work-dir>}"
mkdir -p "${work_dir}"
work_dir="$(cd "${work_dir}" && pwd)"

checkout() {  # <name> -> prints the source directory
  local name="$1" commit src
  commit="$(pinned "${name}")"
  src="${work_dir}/${name}/src"
  if [[ -z "${commit}" ]]; then
    echo "#E: no pinned commit for ${name} in ${manifest}" >&2; exit 4
  fi
  if [[ ! -d "${src}/.git" ]]; then
    git clone --quiet "$(url "${name}")" "${src}"
  fi
  git -C "${src}" fetch --quiet origin
  git -C "${src}" checkout --quiet --detach "${commit}"
  if [[ -n "$(git -C "${src}" status --porcelain)" ]]; then
    echo "#E: ${src} has local changes; refusing to build an unpinned tree" >&2; exit 4
  fi
  if [[ "$(git -C "${src}" rev-parse HEAD)" != "${commit}" ]]; then
    echo "#E: ${src} is not at ${commit}" >&2; exit 4
  fi
  echo "${src}"
}

hul_src="$(checkout hul-common-lib)"
hul_install="${work_dir}/hul-common-lib/install"
cmake -S "${hul_src}" -B "${work_dir}/hul-common-lib/build" -DCMAKE_INSTALL_PREFIX="${hul_install}"
cmake --build "${work_dir}/hul-common-lib/build" --parallel "$(nproc)"
cmake --install "${work_dir}/hul-common-lib/build"

amq_src="$(checkout amaneq-soft)"
amq_install="${work_dir}/amaneq-soft/install"
cmake -S "${amq_src}" -B "${work_dir}/amaneq-soft/build" -DCMAKE_INSTALL_PREFIX="${amq_install}" \
  -DCMAKE_PREFIX_PATH="${hul_install}"
cmake --build "${work_dir}/amaneq-soft/build" --parallel "$(nproc)"
cmake --install "${work_dir}/amaneq-soft/build"

echo "#D: hul-common-lib $(git -C "${hul_src}" rev-parse HEAD) -> ${hul_install}"
echo "#D: amaneq-soft    $(git -C "${amq_src}" rev-parse HEAD) -> ${amq_install}"
find "${hul_install}" "${amq_install}" -path "*/bin/*" -type f -perm -u+x -print0 | sort -z | xargs -0 sha256sum
