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

- `scripts/run-timeframe-chain.py` — the default chain. One `AmQStrTdcSampler` → `STFBuilder` per entry of `data_sources`
  in the setup file (for example the Receiver and several HR-TDC boards), all into one `TimeFrameBuilder` → `FileSink`. Each STF holds one heartbeat frame,
  so the TimeFrameBuilder joins the sources by heartbeat-frame number. Output: `runXXXXXX.dat` in the storage directory.
  - `--live` reads the boards in the setup file; `--set-hbfstate` switches the root HBF on for the capture and off afterwards.
  - `--sources NAME ...` uses only the named `data_sources` entries, in that order (default: all, in setup order).
    Service names follow the entry names: `hrtdc2` becomes `Hrtdc2Input` → `Hrtdc2STF`.
  - `--replay FILE ... --sender build/devices/StfPartSender` feeds saved STFs instead of boards, one file per selected source
    (prepare the inputs with `tools/replay_timeframe_inputs.py`).
  - `--sc-filter-prescale N --e88-param INDEX` also runs the SC coincidence filter (`../online-filter`, device
    `e88-sc-filter-device`, built in `build/online-filter`; `--sc-filter-exe` gives another path) on the `dqm` side output
    of the TimeFrameBuilder, and a second FileSink (`FilterSink`) writes the kept time frames to `runXXXXXX_sc.dat` next to
    the run file, with `runXXXXXX_sc.marks.tsv` and `runXXXXXX_sc.summary.json`. The run file is unchanged: the TimeFrameBuilder
    sends to the side output only when the filter can take the time frame. The summaries are copied into the chain report.
    `--prescale-filter N` runs the plain prescale (1 of N time frames, content not read, no parameters) the same way and writes
    `runXXXXXX_ps.dat`. One side-output filter per session. Its kind (`sc`, `prescale`, `off`) and N can be changed between runs:
    the filter reads `run-settings.txt` in the output directory at every RUN (written at the start, replaced by the session page),
    the file names keep the suffix of the session, and the summary of each run records the kind and N used.
  - `--webctl ADDR:PORT` starts `daq-webctl` (the NestDAQ web controller) at that address and leaves the state transitions,
    RUN, STOP, the run number, and END to the browser (`http://ADDR:PORT/daq-webctl.html`). The page is `webctl/daq-webctl.html` of this
    directory (given with `--doc-root`; based on the NestDAQ page, MIT License in `webctl/LICENSE-nestdaq`): its state transition
    buttons can be pressed only when every chosen device is in the state the transition starts from (none during a transition, or for
    up to 5 s after a press until the states change; only RESET DEVICE and END PROCESS, with a confirmation, when the devices disagree,
    one is in ERROR, or a transition has not moved for 30 s; none when the page has lost the controller), it colours the cell of the
    current state of each service, shows whether the session saves data (the newest run file and its size, or "NOT SAVING DATA" for a
    `--no-file` session), sets the side-output filter for the next RUN, and sets the run comment. APPLY LATEST applies the filter
    settings applied most recently from the page and LATEST fills in the comment set most recently (press SET to use it), also from
    an earlier session. The last three use the session page
    (`../run-control/session-server.py`) under the same proxy and show "unknown" when it cannot be reached. `scripts/webctl-hook.py` runs around
    RUN and STOP: after RUN it waits until every sampler has connected and then switches the root HBF on; before STOP it switches it off.
    The session ends when every device has exited (END) or on Ctrl-C/SIGTERM, and the HBF is switched off then as well.
    Hook results are in `hooks.jsonl` in the output directory; the post-run record also holds the run number and the run comment.
    The run comment is the Redis key `run_info:run_comment` of the session: the FileSink writes it into the header and trailer of
    the run file at RUN (at most 255 bytes). daq-webctl itself cannot set it, so the controller page sets it through the session page. Bind to 127.0.0.1 and reach it through an SSH tunnel
    or the password-protected proxy of the experiment portal.
    `daq-webctl` aborts when a browser drops its WebSocket abruptly; it keeps no state of its own, so the launcher restarts it
    (recorded as `webctl_restarts` in `report.json`) and the devices and the run continue.
  - `--analyzer-http ADDR:PORT` runs the online analyzer (`../online-analyzer`, `nda-replay --follow`) on the run file being
    written and serves its histograms at that address. It only reads the file, at a lower priority than the DAQ processes, so it
    cannot slow the DAQ down. A new run file (a new run number at RUN) gets a new analyzer; the last one keeps serving until the
    next run or the end of the session. If it fails with the E88 parameters (`--e88-param`), it is restarted once without them.
    A failing analyzer or a port in use is recorded in `online_analyzer` of `report.json` and never stops the run.
    The page refreshes the displayed histograms every few seconds (Monitoring) and has two buttons at the top: Reset clears the
    histograms and counters, which then fill again from the next time frame, and Restart starts the analyzer again on the same run
    file from its beginning (the page is unavailable for a few seconds; recorded in `online_analyzer` of `report.json`).
  - `--no-file` starts no FileSink, so no run file is written; the TimeFrameBuilder runs with `--discard-output true` and
    discards the time frames. Its error line for every discarded time frame (about 0.1 kB, some 0.7 GB per hour) is left out of
    `TimeFrameBuilder.log`: the launcher counts these lines, writes one summary line per minute, and records the total in
    `log_lines_left_out` of `report.json`. Side-output filters are skipped (recorded as
    `side_filter_skipped`). With `--analyzer-http`, `nda-online-monitor` reads the `dqm` side output instead of a file: the
    TimeFrameBuilder sends to it only when it can take a time frame, so it never holds the DAQ back. The device takes its run number
    when it starts (the session's first run number), answers HTTP only while RUNNING, and may fail without stopping the session.
    Its page has only the Reset button: the device is part of the chain and is not restarted from the page.
  - Every invocation starts its own Redis on 127.0.0.1 (with the RedisTimeSeries module that TimeFrameBuilder needs for metrics)
    and stops only the processes it started.
- `scripts/run-board-chain.sh` — one board: `AmQStrTdcSampler` → `STFBuilder` → `FileSink`.
- `scripts/run-separate-stf.py` — one `run-board-chain.sh` per board and one shared HBF window from the root
  (the older layout with a separate file per board).

## Online analyzer

The online analyzer is the pinned submodule `../online-analyzer`. Build it in `build/online-analyzer` (both executables;
`nda-online-monitor` needs FairMQ from the NestDAQ prefix):

```bash
set -a; . config/local/nestdaq.env; set +a
cmake -S online-analyzer -B build/online-analyzer -DNDA_BUILD_DEVICES=ON \
      -DCMAKE_PREFIX_PATH="$NESTDDAQ_PREFIX" -DBOOST_ROOT="$NESTDDAQ_PREFIX"
cmake --build build/online-analyzer -j
E88PARAM_DIR=<e88param> ctest --test-dir build/online-analyzer
```

The analyzer of a session is the pinned build above; move the pin only between sessions. To try another analyzer version
while a session runs, build it in a clone of your own (branch `dev` or a branch from it) and run it with
`tools/analyzer-dev.py` of that clone: it only reads the run file, so it does not touch the DAQ or the analyzer of the
session, and it can compare its results with those of the pinned build (see `online-analyzer/README.md`, "Developing next to
a running DAQ"). The page of the session's analyzer has a Reset for one browser only, next to the Reset that clears the
histograms for everybody.

## Devices

- `devices/StfPartSender` — sends saved STF multipart messages (one message per line, hex parts) for replay tests.

## Topology

`topology/` will hold the device chains for other board setups (more HR-TDC boards, online analyzer and filter outputs).
