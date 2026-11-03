#!/usr/bin/env bash
# Read-only host survey for NestDAQ preparation. Does not install packages or clone.
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out_dir="${1:-${repository_root}/build/nestdaq-prereq}"
mkdir -p "${out_dir}"
report="${out_dir}/prerequisites.txt"

have() {
  command -v "$1" >/dev/null 2>&1
}

version_or_missing() {
  local name="$1"
  shift
  if have "${name}"; then
    "$@" 2>/dev/null | awk 'NR==1 {print; exit}'
  else
    echo "not_found"
  fi
}

{
  echo "nestdaq prerequisite survey"
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "os_pretty: $(. /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-UNBOUND}" || echo UNBOUND)"
  echo "kernel: $(uname -r)"
  echo "arch: $(uname -m)"
  echo "product_name: $(cat /sys/class/dmi/id/product_name 2>/dev/null || echo UNBOUND)"
  echo "product_family: $(cat /sys/class/dmi/id/product_family 2>/dev/null || echo UNBOUND)"
  echo "git: $(version_or_missing git git --version)"
  echo "python3: $(version_or_missing python3 python3 --version)"
  echo "gcc: $(version_or_missing gcc gcc --version)"
  echo "g++: $(version_or_missing g++ g++ --version)"
  echo "cmake: $(version_or_missing cmake cmake --version)"
  echo "make: $(version_or_missing make make --version)"
  echo "ninja: $(version_or_missing ninja ninja --version)"
  echo "podman: $(version_or_missing podman podman --version)"
  echo "docker: $(version_or_missing docker docker --version)"
  echo "redis-server: $(version_or_missing redis-server redis-server --version)"
  echo "root-config: $(version_or_missing root-config root-config --version)"
  echo "fairmq-config: $(version_or_missing fairmq-config fairmq-config --version)"
  echo "NESTDDAQ_PREFIX: ${NESTDDAQ_PREFIX:-unset}"
  if [[ -n "${NESTDDAQ_PREFIX:-}" && -d "${NESTDDAQ_PREFIX}" ]]; then
    echo "nestdaq_prefix_exists: yes"
  else
    echo "nestdaq_prefix_exists: no"
  fi
  echo "ethernet_sysfs_present: $(if [[ -d /sys/class/net ]]; then echo yes; else echo no; fi)"
  echo "note: IP addresses, MAC addresses, hostnames, and usernames are omitted"
} > "${report}"

echo "wrote ${report}"
cat "${report}"
