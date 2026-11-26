# run-control

`daqctl.py` checks the boards listed in the setup file and takes runs.

```bash
set -a; . config/local/nestdaq.env; set +a
CTL=<work-dir of amaneq/build-control-tools.sh>
READER=$CTL/hul-common-lib/install/bin/read_register
WRITER=$CTL/hul-common-lib/install/bin/write_register
SET_HBFSTATE=$CTL/hul-common-lib/install/bin/set_hbfstate
INITIALIZE=$CTL/amaneq-soft/install/StrHRTDC/bin/initialize

run-control/daqctl.py status  --reader "$READER" --out <new-dir>
run-control/daqctl.py prepare --reader "$READER" --initialize "$INITIALIZE" --out <new-dir>
run-control/daqctl.py run     --reader "$READER" --writer "$WRITER" --set-hbfstate "$SET_HBFSTATE" \
                              --run-number <E88 run number> --seconds 10 --out <new-dir>
```

- The setup file is `config/local/setup.yaml` (template: `config/setup.yaml.example`); pass another one with `--manifest`.
  Every entry of `data_sources` needs a board entry of the same name under `operational_control.boards`, and exactly one board has the role `receiver`.
  A board whose `link_mask` is `null` is reported but never counts as linked, so `status` shows its link registers and `prepare`/`run` stop.
- `status` checks for every board: the host route and MAC, the firmware identity, the links, and for HR-TDC the receiver enable and DDR alignment.
  It also checks that the root HBF is off.
- `prepare` runs `initialize` only on HR-TDC boards that are not ready, and checks the success messages and the state afterwards.
  After power-up or a reset, run `status` and, if needed, `prepare`.
- `run` requires every board ready and the root HBF off. It enables the Receiver record output (and restores it afterwards),
  starts `nestdaq/scripts/run-timeframe-chain.py --live`, switches the root HBF on for the capture and off at the end,
  and checks that the HBF is off again. `--builder separate-stf` uses the older one-file-per-board layout.
- `run --webctl 127.0.0.1:<port>` takes the run from the web controller instead of a fixed `--seconds` capture: initialize the devices,
  press RUN and STOP as often as needed (change the run number in the page between runs), and press END to finish.
  The Receiver output stays enabled for the whole session and is restored afterwards, as in a normal run.
- `run --sc-filter-prescale N --e88-param <e88param>/<series>/index.txt` (timeframe run) also writes `runXXXXXX_sc.dat`, the time frames
  kept by the SC coincidence filter on the TimeFrameBuilder side output (`../online-filter`), with an unbiased prescale of 1 in N.
  `run --prescale-filter N` writes `runXXXXXX_ps.dat` with the plain prescale (1 of N time frames) instead.
  Build the filter first (`../online-filter/README.md`).
- `run --analyzer-http 127.0.0.1:<port>` (timeframe run) also runs the online analyzer (`../online-analyzer`) and serves its
  histograms there (ROOT THttpServer / JSROOT). It follows the run file being written (`nda-replay --follow`), so it never
  slows the DAQ down; each new run file gets a new analyzer, and the last one keeps serving until the session ends.
  `--e88-param` gives it the E88 parameters as well. Build the analyzer first (`../nestdaq/README.md`).
- `run --no-file` (timeframe run) writes no run file: there is no FileSink, and the TimeFrameBuilder discards the time frames.
  Side-output filters are skipped. With `--analyzer-http` the analyzer reads the data stream instead (the TimeFrameBuilder side
  output, best effort: time frames the analyzer cannot take in time are skipped for it). The result is `NO_FILE`.
- `session-server.py` serves a small page (loopback only, behind the password-protected proxy of the experiment portal) that
  starts such a `--webctl` session with an explicit run number and shows the result of the last one. A checkbox starts the
  session with `--no-file`. For the controller page of the running session it also answers `recording.json` (whether the session
  saves data, and the newest run file and its size), `run-settings.json` / `run-settings` (the side-output filter for the next RUN;
  changes are logged in `run-settings.jsonl` of the session directory), and `run-comment.json` / `run-comment` (the run comment,
  one line of at most 255 bytes, used from the next RUN: the FileSink writes it into the header and trailer of the run file and
  the run hook records it in `hooks.jsonl`; changes are logged in `run-comments.jsonl`). Before starting it checks that no session or other board-touching process is running and that the
  run file does not exist. The command it runs is set in `config/local/session.env` (template: `config/session.env.example`).
  When no session is running, the **Prepare boards** button runs `SESSION_PREPARE_COMMAND` (normally `daqctl.py prepare`) with
  the same checks, in a new `<date>-prepare` directory, and shows its result and the boards that are not READY (also as
  `prepare.json`). No session can start while it runs. Without `SESSION_PREPARE_COMMAND` the button is not shown.
- The run number is given explicitly; the data file is `runXXXXXX.dat` (six digits) in the storage directory, and an existing file is never overwritten.
- `<new-dir>` must not exist. `report.json` there records the result, every command, and every response.
- Any unexpected response stops the action. Nothing is retried, reset, or repaired automatically.

`CAPTURE_COMPLETED_REQUIRES_DECODE` means the processes ended normally and data can exist: the data file of the run number is
there, or (with `--webctl`) RUN was pressed at least once (`runs_started`; if the run number was changed in the page, the file
has another name and `note` says so). Check the data with `../tools/` before trusting it. `NO_DATA` means the session ended
without data: no RUN was pressed in the web controller, or a fixed capture wrote no file. `data_file_exists` and
`data_file_bytes` are recorded in both cases. `NO_FILE` is the result of a `--no-file` session (with `runs_started`).

Planned: run list entries (YAML) per run, configuration snapshots with SHA-256, and run start/stop records.
