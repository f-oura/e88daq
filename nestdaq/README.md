# nestdaq

NestDAQ build scripts, run scripts, and E88-specific devices.

## Build NestDAQ (once per host)

Upstream sources are not modified. Their commits are pinned in `../manifests/upstream.yaml`;
sources, build trees, and the install prefix live outside this repository.

```bash
cp config/local.env.example config/local/nestdaq.env   # fill in the paths
set -a; . config/local/nestdaq.env; set +a
nestdaq/scripts/check-prerequisites.sh                  # read-only host survey
nestdaq/scripts/fetch-upstream.sh                       # prints the clone plan
NESTDAQ_FETCH_APPROVED=yes nestdaq/scripts/fetch-upstream.sh --execute
nestdaq/scripts/prepare-nestdaq-source-overlay.sh       # build copy with the Redis subscriber-shutdown and FairMQ link fixes
cmake -DCMAKE_PREFIX_PATH="$NESTDDAQ_PREFIX" -DCMAKE_INSTALL_PREFIX="$NESTDDAQ_PREFIX" \
  -B "$NESTDDAQ_BUILD" -S "$NESTDDAQ_SOURCE_OVERLAY"
cmake --build "$NESTDDAQ_BUILD" --parallel && cmake --install "$NESTDDAQ_BUILD"
nestdaq/scripts/prepare-user-impl-build-copy.sh         # build copy without RHTTP, linked through FairMQ::FairMQ
cmake -DCMAKE_PREFIX_PATH="$NESTDDAQ_PREFIX;$NESTDDAQ_FETCH_DIR/uhbook" -DCMAKE_INSTALL_PREFIX="$NESTDDAQ_PREFIX" \
  -B "$NESTDDAQ_USER_IMPL_BUILD" -S "$NESTDDAQ_USER_IMPL_BUILD_COPY"
cmake --build "$NESTDDAQ_USER_IMPL_BUILD" --parallel && cmake --install "$NESTDDAQ_USER_IMPL_BUILD"
nestdaq/scripts/build-devices.sh                        # devices in nestdaq/devices (build/devices)
```

## Run scripts

Normally these are started by `run-control/daqctl.py`, not by hand.

- `scripts/run-timeframe-chain.py` — the default chain. One `AmQStrTdcSampler` → `STFBuilder` per data source
  (Receiver and HR-TDC), both into one `TimeFrameBuilder` → `FileSink`. Each STF holds one heartbeat frame,
  so the TimeFrameBuilder joins the sources by heartbeat-frame number. Output: `runXXXXXX.dat` in the storage directory.
  - `--live` reads the boards in the setup file; `--set-hbfstate` switches the root HBF on for the capture and off afterwards.
  - `--replay RECEIVER_MESSAGES HRTDC_MESSAGES --sender build/devices/StfPartSender` feeds saved STFs instead of boards
    (prepare the inputs with `tools/replay_timeframe_inputs.py`).
  - Every invocation starts its own Redis on 127.0.0.1 (with the RedisTimeSeries module that TimeFrameBuilder needs for metrics)
    and stops only the processes it started.
- `scripts/run-board-chain.sh` — one board: `AmQStrTdcSampler` → `STFBuilder` → `FileSink`.
- `scripts/run-separate-stf.py` — one `run-board-chain.sh` per board and one shared HBF window from the root
  (the older layout with a separate file per board).

## Devices

- `devices/StfPartSender` — sends saved STF multipart messages (one message per line, hex parts) for replay tests.

## Topology

`topology/` will hold the device chains for other board setups (more HR-TDC boards, online analyzer and filter outputs).
