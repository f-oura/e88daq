#!/usr/bin/env bash
# Prepare a disposable nestdaq-user-impl source copy for this host's ROOT and
# FairMQ ABI. The upstream checkout is never modified.
set -euo pipefail

if [[ -z "${NESTDDAQ_USER_IMPL:-}" || ! -d "${NESTDDAQ_USER_IMPL}" ]]; then
  echo "#E: set NESTDDAQ_USER_IMPL to the pinned upstream checkout" >&2
  exit 2
fi
if [[ -z "${NESTDDAQ_USER_IMPL_BUILD_COPY:-}" ]]; then
  echo "#E: set NESTDDAQ_USER_IMPL_BUILD_COPY to a Git-ignored build-copy path" >&2
  exit 2
fi

source_file="${NESTDDAQ_USER_IMPL}/CMakeLists.txt"
destination="${NESTDDAQ_USER_IMPL_BUILD_COPY}"
expected='find_package(ROOT REQUIRED COMPONENTS RIO RHTTP Hist)'
replacement='find_package(ROOT REQUIRED COMPONENTS RIO Hist)'

if ! grep -Fqx "${expected}" "${source_file}"; then
  echo "#E: pinned upstream CMakeLists does not match the reviewed ROOT component line" >&2
  exit 3
fi

if [[ -e "${destination}" ]]; then
  echo "#E: build-copy path already exists; inspect it before replacing: ${destination}" >&2
  exit 4
fi

mkdir -p "$(dirname "${destination}")"
cp -a "${NESTDDAQ_USER_IMPL}/." "${destination}/"
sed -i "s/${expected//\//\\/}/${replacement//\//\\/}/" "${destination}/CMakeLists.txt"

python3 - "${destination}/CMakeLists.txt" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
marker = "FairMQ_VERSION VERSION_EQUAL 1.10.0"
positions = []
offset = 0
while True:
    found = text.find(marker, offset)
    if found < 0:
        break
    positions.append(found)
    offset = found + len(marker)
if len(positions) != 3:
    raise SystemExit("expected three FairMQ 1.10.0 branches")

for found in reversed(positions):
    link = text.find("fairmq;", found)
    if link < 0:
        raise SystemExit("expected one raw fairmq link in each 1.10.0 branch")
    text = text[:link] + "FairMQ::FairMQ;" + text[link + len("fairmq;"):]

path.write_text(text)
PY

if ! grep -Fqx "${replacement}" "${destination}/CMakeLists.txt" \
  || [[ "$(grep -c 'FairMQ::FairMQ;' "${destination}/CMakeLists.txt")" -ne 3 ]]; then
  echo "#E: failed to apply the reviewed ROOT and FairMQ adjustments" >&2
  exit 5
fi

echo "#D: prepared build-only copy at ${destination}"
echo "#D: upstream checkout remains unchanged"
