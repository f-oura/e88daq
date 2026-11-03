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
- `status` checks for every board: the host route and MAC, the firmware identity, the links, and for HR-TDC the receiver enable and DDR alignment.
  It also checks that the root HBF is off.
- `prepare` runs `initialize` only on HR-TDC boards that are not ready, and checks the success messages and the state afterwards.
  After power-up or a reset, run `status` and, if needed, `prepare`.
- `run` requires every board ready and the root HBF off. It enables the Receiver record output (and restores it afterwards),
  starts `nestdaq/scripts/run-timeframe-chain.py --live`, switches the root HBF on for the capture and off at the end,
  and checks that the HBF is off again. `--builder separate-stf` uses the older one-file-per-board layout.
- The run number is given explicitly; the data file is `runXXXXXX.dat` (six digits) in the storage directory, and an existing file is never overwritten.
- `<new-dir>` must not exist. `report.json` there records the result, every command, and every response.
- Any unexpected response stops the action. Nothing is retried, reset, or repaired automatically.

`CAPTURE_COMPLETED_REQUIRES_DECODE` means the processes ended normally; check the data with `../tools/` before trusting it.

Planned: run list entries (YAML) per run, configuration snapshots with SHA-256, and run start/stop records.
