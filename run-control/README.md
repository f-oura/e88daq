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
- `session-server.py` serves a small page (loopback only, behind the password-protected proxy in `../web/`) that starts
  such a `--webctl` session with an explicit run number and shows the result of the last one. Before starting it checks that
  no session or other board-touching process is running and that the run file does not exist. The command it runs is set in
  `config/local/session.env` (template: `config/session.env.example`).
- The run number is given explicitly; the data file is `runXXXXXX.dat` (six digits) in the storage directory, and an existing file is never overwritten.
- `<new-dir>` must not exist. `report.json` there records the result, every command, and every response.
- Any unexpected response stops the action. Nothing is retried, reset, or repaired automatically.

`CAPTURE_COMPLETED_REQUIRES_DECODE` means the processes ended normally and data can exist: the data file of the run number is
there, or (with `--webctl`) RUN was pressed at least once (`runs_started`; if the run number was changed in the page, the file
has another name and `note` says so). Check the data with `../tools/` before trusting it. `NO_DATA` means the session ended
without data: no RUN was pressed in the web controller, or a fixed capture wrote no file. `data_file_exists` and
`data_file_bytes` are recorded in both cases.

Planned: run list entries (YAML) per run, configuration snapshots with SHA-256, and run start/stop records.
