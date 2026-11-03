#!/usr/bin/env bash
# NestDAQ chain for one AMANEQ board: AmQStrTdcSampler -> STFBuilder -> FileSink.
#
# Redis binds 127.0.0.1 only and
# daq-webctl is never started. The chain opens the explicitly supplied board's
# SiTCP data socket (TCP 24) read-only; it issues no RBCP read or write at all.
# The heartbeat-frame state is not changed here: frames flow only while Root
# has set_hbfstate on, which is a separate step (see run-separate-stf.py).
#
# TdcType defaults to 2: nestdaq-user-impl dc2985f7 consumers treat femType 2
# as HR-TDC (AmQStrTdcDqm.cxx:225,231; Scaler.cxx:76), although an older
# data format note lists 1 as HR. The value only labels the STF
# header (AmQStrTdcSampler.cxx:175, STFBuilder.cxx:256).
#
# Usage: run-board-chain.sh <out-dir> <seconds> <board-ip>
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repository_root}"

out_dir="${1:?usage: $0 <out-dir> <seconds> <board-ip>}"
duration="${2:?usage: $0 <out-dir> <seconds> <board-ip>}"

# A whitespace-only value passes bash's ${1:?...} empty-string check (it is not
# the empty string) but is not a usable directory name.
if [[ -z "${out_dir//[[:space:]]/}" ]]; then
  echo "#E: out-dir must not be empty or whitespace-only" >&2; exit 4
fi

# Normalise to an absolute path before anything else touches it. A relative
# out_dir once failed before any board was contacted: redis.conf below writes
# both `dir` and `logfile` as whatever out_dir was passed, and Redis resolves
# a relative `logfile` relative to `dir` -- its own chdir target -- not the
# invoking shell's directory. Absolutising here removes the ambiguity regardless of how this
# script is invoked.
if [[ "${out_dir}" != /* ]]; then
  out_dir="$(pwd)/${out_dir}"
fi

parent_dir="$(dirname "${out_dir}")"
if [[ ! -d "${parent_dir}" ]]; then
  echo "#E: parent directory does not exist: ${parent_dir}" >&2; exit 4
fi
if [[ ! -w "${parent_dir}" ]]; then
  echo "#E: parent directory is not writable: ${parent_dir}" >&2; exit 4
fi

# Refuse to reuse or overwrite an existing run rather than silently proceeding
# into it: this script deletes and recreates out_dir/redis unconditionally
# further down, which would remove a prior run's live Redis instance out from
# under it if the same directory were ever passed twice.
if [[ -e "${out_dir}" ]]; then
  if [[ ! -d "${out_dir}" ]]; then
    echo "#E: out-dir exists and is not a directory: ${out_dir}" >&2; exit 4
  fi
  if [[ -n "$(ls -A "${out_dir}" 2>/dev/null)" ]]; then
    echo "#E: out-dir already has content; refusing to reuse or overwrite a prior run: ${out_dir}" >&2
    exit 4
  fi
fi

mkdir -p "${out_dir}"
if [[ ! -w "${out_dir}" ]]; then
  echo "#E: out-dir is not writable: ${out_dir}" >&2; exit 4
fi

# A unique numeric tag per chain invocation, used to build each device's
# FairMQ --id as "<ServiceName>-<chain_tag>" below, so that two chains never
# collide on the same abstract IPC socket path
# (/tmp/nestdaq/<service>/<id>/<channel>). Without this, every device leaves
# its "id" property empty, and daq_service's DaqServicePlugin::SetId() then
# assigns the first free instance-index (0, 1, ...) from a Redis-backed
# counter scoped only to that Redis instance -- so two chains on two
# separate, independently-allocated Redis ports each still allocate index 0
# for the same service name, producing identical ids and therefore identical
# socket paths. Passing --id here bypasses that counter entirely (DaqService
# Plugin::SetId() only falls back to it when "id" is not already set).
#
# The tag must be purely numeric, not an arbitrary string: AmQStrTdcSampler's
# msiTcpIp/TdcType parameters are injected via the parameter_config plugin,
# whose ReadParameters() (nestdaq plugins/ParameterConfigPlugin.cxx) builds
# its Redis lookup key as "parameters:<id>" and separately derives a
# service-level group key by stripping everything from the *last* "-" in
# that id onward, but only when what follows that last "-" is entirely
# digits. An id such as "AmQStrTdcSampler-a1b2c3d4" would make that check
# fail, silently losing the "parameters:AmQStrTdcSampler" group-level
# lookup this script's own r2 hset calls below rely on. A per-invocation
# numeric tag keeps that convention intact.
#
# The tag is this script's own PID ($$): always a positive decimal integer
# (so it is never empty and needs no character normalisation), bounded to
# at most 7 digits on this platform (well within any IPC path length
# limit), and -- the actual property this fix needs -- guaranteed distinct
# between any two chains that are genuinely running at the same time, since
# the kernel never assigns the same PID to two simultaneously-live
# processes. Same-chain peer discovery is unaffected by any of this:
# TopologyConfig resolves a peer's actual id at runtime via a Redis key scan
# keyed on service name, not by assuming any particular id value.
chain_tag="$$"
if [[ ! "${chain_tag}" =~ ^[0-9]+$ ]]; then
  echo "#E: failed to derive a well-formed numeric chain tag (got '${chain_tag}')" >&2; exit 4
fi
echo "#D: chain_tag=${chain_tag} (this script's PID, used to build each device's FairMQ --id)"

if [[ -f config/local/nestdaq.env ]]; then
  set -a
  # shellcheck disable=SC1091
  . config/local/nestdaq.env
  set +a
fi

# The endpoint is an explicit positional argument; AMANEQ_SITCP_IP is never read.
board_ip="${3:-}"
tdc_type="${HRTDC_TDC_TYPE:-2}"

if [[ -z "${board_ip}" || "${board_ip}" == "UNBOUND" ]]; then
  echo "#E: board address is required as the third positional argument" >&2; exit 4
fi
if [[ -z "${NESTDDAQ_PREFIX:-}" || ! -d "${NESTDDAQ_PREFIX}" ]]; then
  echo "#E: NESTDDAQ_PREFIX unset or missing" >&2; exit 4
fi

sampler="${NESTDDAQ_PREFIX}/bin/AmQStrTdcSampler"
stfbuilder="${NESTDDAQ_PREFIX}/bin/STFBuilder"
filesink="${NESTDDAQ_PREFIX}/bin/FileSink"
redis_server="${NESTDDAQ_PREFIX}/bin/redis-server"
redis_cli="${NESTDDAQ_PREFIX}/bin/redis-cli"
plugin_libdir="${NESTDDAQ_PREFIX}/lib64"

for required in "${sampler}" "${stfbuilder}" "${filesink}" "${redis_server}" "${redis_cli}"; do
  [[ -x "${required}" ]] || { echo "#E: missing ${required}" >&2; exit 4; }
done

redis_port="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
redis_dir="${out_dir}/redis"
rm -rf "${redis_dir}"; mkdir -p "${redis_dir}"
cat > "${redis_dir}/redis.conf" <<EOF
bind 127.0.0.1 -::1
port ${redis_port}
protected-mode yes
daemonize no
dir ${redis_dir}
save ""
appendonly no
logfile ${out_dir}/redis.log
EOF

sampler_pid=""; stfb_pid=""; filesink_pid=""; redis_pid=""
cleanup() {
  local pid
  for pid in "${sampler_pid}" "${stfb_pid}" "${filesink_pid}" "${redis_pid}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
      wait "${pid}" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT

"${redis_server}" "${redis_dir}/redis.conf" & redis_pid=$!
for _ in $(seq 1 50); do
  "${redis_cli}" -h 127.0.0.1 -p "${redis_port}" ping 2>/dev/null | grep -q PONG && break
  sleep 0.1
done
"${redis_cli}" -h 127.0.0.1 -p "${redis_port}" ping | grep -q PONG \
  || { echo "#E: redis did not answer" >&2; exit 1; }

r() { "${redis_cli}" -h 127.0.0.1 -p "${redis_port}" "$@" >/dev/null; }
# parameter_config reads database 2 (its URI ends in /2).
r2() { "${redis_cli}" -h 127.0.0.1 -p "${redis_port}" -n 2 "$@" >/dev/null; }
r SET run_info:run_number 1
r hset daq_service:topology:endpoint:AmQStrTdcSampler:out type push method bind autoSubChannel false waitForPeerConnection false
r hset daq_service:topology:endpoint:STFBuilder:in         type pull method connect autoSubChannel false
r hset daq_service:topology:endpoint:STFBuilder:out        type push method bind autoSubChannel false waitForPeerConnection false
r hset daq_service:topology:endpoint:FileSink:in           type pull method connect autoSubChannel false
r set daq_service:topology:link:AmQStrTdcSampler:out,STFBuilder:in none
r set daq_service:topology:link:STFBuilder:out,FileSink:in none
# AmQStrTdcSampler reads msiTcpIp and TdcType as properties it does not declare,
# so they can only be injected through the Redis-backed parameter_config plugin.
r2 hset parameters:AmQStrTdcSampler   msiTcpIp "${board_ip}" TdcType "${tdc_type}"
r2 hset "parameters:AmQStrTdcSampler-${chain_tag}" msiTcpIp "${board_ip}" TdcType "${tdc_type}"

# Write each device's Redis presence key (daq_service:<ServiceName>:<id>:
# presence) ourselves, before any device is launched, rather than waiting
# for the device to write its own (an earlier fix here tried exactly that
# wait, and it never succeeded). Tracing every assignment to fPresence->key in
# nestdaq's DaqServicePlugin.cxx (machine-local checkout, not under this
# repository; path not recorded here) shows it is assigned in exactly one
# place: inside SetId()'s auto-numbering branch, which runs only when a
# device's "id" property was left empty. Passing an explicit --id (needed
# for the IPC-collision fix above) makes that whole branch -- and this one
# assignment -- never run, so the real device never writes this key at all,
# not merely late. Presence is read at two points in the connect sequence,
# both during TopologyConfig::Initialize()/WaitBindAddress() (nestdaq
# plugins/TopologyConfig.cxx): a channel whose peer's presence is missing
# at that moment gets an empty peer list written once and never revisited,
# permanently locking its connect address at "unspecified". Writing the key
# ourselves before any device starts guarantees it already exists by the
# time any device reaches that point, regardless of process-launch timing.
#
# The TTL here (60s) is a generous multiple of the ~4s this whole chain
# needs from launch to CONNECT in every localhost trial performed for this
# fix (see the same evidence file), not a continuously-refreshed value like
# the real mechanism's own (3s refresh / 5s TTL): the primary safety net
# against a placeholder outliving a dead device is this script's own
# aliveness check and cleanup() trap below, which tear down this chain's
# entire dedicated Redis instance -- placeholder included -- as soon as a
# problem is noticed, faster in every observed case than this TTL. A
# continuously-refreshed, PID-tied placeholder was considered as a more
# complete alternative and is documented, but not implemented, in that same
# evidence file.
for svc in FileSink STFBuilder AmQStrTdcSampler; do
  r SETEX "daq_service:${svc}:${svc}-${chain_tag}:presence" 60 "launcher-placeholder-${chain_tag}"
done

export LD_LIBRARY_PATH="${plugin_libdir}:${NESTDDAQ_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
plugin_args=(
  -S "<${plugin_libdir}"
  -P daq_service -P parameter_config
  --registry-uri "tcp://127.0.0.1:${redis_port}/0"
  --parameter-config-uri "tcp://127.0.0.1:${redis_port}/2"
  --severity debug --color 0
)
ulimit -c 0

mkdir -p "${out_dir}/data"
"${filesink}" "${plugin_args[@]}" --id "FileSink-${chain_tag}" \
  --in-chan-name in --prefix "${out_dir}/data" --ext .dat --openmode create \
  --discard false --multipart false \
  > "${out_dir}/filesink.log" 2>&1 & filesink_pid=$!
"${stfbuilder}" "${plugin_args[@]}" --id "STFBuilder-${chain_tag}" \
  --in-chan-name in --out-chan-name out --fem-id 0 --max-hbf 1 \
  --split 0 --time-frame-id-type 0 --discard-output false \
  > "${out_dir}/stfbuilder.log" 2>&1 & stfb_pid=$!
"${sampler}" "${plugin_args[@]}" --id "AmQStrTdcSampler-${chain_tag}" --out-chan-name out \
  > "${out_dir}/sampler.log" 2>&1 & sampler_pid=$!
# A basic process-startup settle, unrelated to the presence-registration
# question above (that is now solved by writing presence before any device
# starts, not by waiting after): the daqctl subscription each device needs
# before it will react to the first "INIT DEVICE" publish below is not
# instantaneous, though drive()'s own multi-attempt retry already tolerates
# a slow one. This value matches what every localhost trial for the
# presence fix above used successfully.
sleep 2

for name in filesink stfb sampler; do
  pid_var="${name}_pid"
  if ! kill -0 "${!pid_var}" 2>/dev/null; then
    echo "#E: ${name} exited before RUN; see ${out_dir}/${name}*.log" >&2
    exit 1
  fi
done

publish() {
  "${redis_cli}" -h 127.0.0.1 -p "${redis_port}" PUBLISH daqctl \
    "$(printf '{"command":"change_state","value":"%s","services":["all"],"instances":["all"]}' "$1")" >/dev/null
}

# Last state each device actually reached, from its own log.
last_state() {
  grep -oE '\] [A-Z ]+---> [A-Z ]+' "${out_dir}/$1.log" 2>/dev/null \
    | tail -1 | sed 's/.*---> //' | sed 's/[[:space:]]*$//'
}

report_states() {
  printf '#D:   %s: sampler=%s | stfbuilder=%s | filesink=%s\n' \
    "$1" "$(last_state sampler)" "$(last_state stfbuilder)" "$(last_state filesink)"
}

# Drive one transition and wait until every device has actually reached the
# target state. The previous attempt published all three transitions before the
# devices had subscribed to daqctl, so nothing was stepped; the publish is
# therefore retried until the states move.
drive() {
  local transition="$1" target="$2" attempt
  for attempt in 1 2 3 4; do
    publish "${transition}"
    local waited
    for waited in $(seq 1 20); do
      if [[ "$(last_state sampler)" == "${target}" \
         && "$(last_state stfbuilder)" == "${target}" \
         && "$(last_state filesink)" == "${target}" ]]; then
        report_states "${transition} -> ${target}"
        return 0
      fi
      sleep 0.5
    done
    echo "#D:   ${transition}: not all devices at ${target} after attempt ${attempt}"
    report_states "retrying"
  done
  echo "#E: ${transition} did not bring every device to ${target}" >&2
  report_states "final"
  return 1
}

report_states "before driving"

# The accepted command strings are space separated, not underscored
# (nestdaq plugins/Constants.h:38-47). INIT_DEVICE was silently ignored.
drive "INIT DEVICE" "INITIALIZED"  || exit 1
drive "BIND"        "BOUND"        || exit 1
drive "CONNECT"     "DEVICE READY" || exit 1
# Both the sampler and STFBuilder enter InitTask here: the sampler sends
# FEMInfo and STFBuilder's blocking receive takes it off the queue.
r2 hset parameters:AmQStrTdcSampler   msiTcpIp "${board_ip}" TdcType "${tdc_type}"
r2 hset "parameters:AmQStrTdcSampler-${chain_tag}" msiTcpIp "${board_ip}" TdcType "${tdc_type}"
sleep 2
drive "INIT TASK"   "READY"        || exit 1
drive "RUN"         "RUNNING"      || exit 1

echo "#D: RUNNING; holding for ${duration} s"
python3 -c "import time; time.sleep(${duration})"
report_states "end of run"
echo "#D: FileSink bytes so far: $(du -cb "${out_dir}"/data/* 2>/dev/null | tail -1 | cut -f1)"

publish STOP
sleep 2
publish END
sleep 3
report_states "after STOP/END"

echo "#D: FileSink files:"; ls -l "${out_dir}/data"
