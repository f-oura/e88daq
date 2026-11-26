# hul/trigger

Parameters for the HUL trigger board and the tools that write them and check the read-back values.
The control programs are those of [jparc-saphire/hul-trigger](https://github.com/jparc-saphire/hul-trigger)
(`software/`), built at the commit pinned in `../../manifests/upstream.yaml`. The FPGA firmware is not kept here.

| File | Purpose |
|---|---|
| `build-tools.sh` | builds the pinned programs with the board address from the host configuration |
| `trgctl.py` | checks a parameter file, writes it, verifies every read-back value, reads the firmware version |
| `param/` | parameter files: `scor.txt`, `scor-clock10k.txt` (`scor.txt` plus the 10 kHz clock on Clock PS, gate always open), `clock1k.txt` (register settings), `nimio.txt`, `nimio-clock10k.txt` (NIM outputs; the second puts the 10 kHz clock on NIM OUT 1 and 2) |

## Build

```bash
cp config/hul-trigger.env.example config/local/hul-trigger.env   # then fill in the address and the work directory
set -a; . config/local/hul-trigger.env; set +a
hul/trigger/build-tools.sh "$HUL_TRIGGER_TOOLS"
```

The upstream programs take the board address from a header at compile time. The script builds a copy of the pinned
sources with that header generated from `HUL_TRIGGER_IP` and records the commit, the address, and the SHA-256 of
every program in `$HUL_TRIGGER_TOOLS/hul-trigger/build-info.json`. Nothing here talks to the board.

## Use

```bash
set -a; . config/local/hul-trigger.env; set +a
hul/trigger/trgctl.py check register hul/trigger/param/scor.txt          # no board access
hul/trigger/trgctl.py version --out <new-dir>                              # reads the firmware version
hul/trigger/trgctl.py write register hul/trigger/param/scor.txt --out <new-dir>
hul/trigger/trgctl.py write nimio hul/trigger/param/nimio.txt --out <new-dir>
```

- `check` compares the file with the upstream reference (`software/param/example.txt` or `software/nim_param/nimio.txt`
  at the pinned commit): missing, unknown, and duplicated keys, and values outside the ranges written in its comments.
  The upstream programs stop at the first missing key after writing the registers before it, and ignore unknown keys,
  so `write` always runs `check` first and refuses a file with errors.
- `write` asks for confirmation (`--yes` skips it), runs the program in `<new-dir>` (it must not exist), and compares
  every written value with the value read back. The result is `OK`, `READBACK_MISMATCH`, or `FAILED`. `<new-dir>` keeps
  a copy of the parameter file, the program's read-back log, and `report.json` (build, address, parameter SHA-256,
  command, output, written and read-back values). A failed write may leave the board partially configured.
  `RGN3::RST_PSCNT` is a reset pulse that the program writes without reading back.
- The tools refuse to run if `HUL_TRIGGER_IP` differs from the address of the build or a program differs from the build record.
- Nothing is retried. `ReconfigFPGA` is built but not wrapped.

## Tests

`../../tests/test_hul_trigger.py` checks `check` without any program. With programs built for `127.0.0.1` it also writes
and reads back every file in `param/` through a loopback RBCP responder (`../../tests/fake_rbcp.py`):

```bash
HUL_TRIGGER_IP=127.0.0.1 hul/trigger/build-tools.sh <dir>
HUL_TRIGGER_TEST_TOOLS=<dir> python3 -m unittest tests.test_hul_trigger
```
