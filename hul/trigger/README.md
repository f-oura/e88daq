# hul/trigger

Parameters for the HUL trigger board and the tools that write them and check the read-back values.
The control software is [jparc-saphire/hul-trigger](https://github.com/jparc-saphire/hul-trigger), kept as a submodule
at a pinned commit. The FPGA firmware is not kept here.

| Path | Purpose |
|---|---|
| `hul-trigger/` | submodule: the upstream sources (`software/`: program sources and reference parameter files) |
| `software/` | the programs built by `build-tools.sh` (`bin/Register`, `bin/NimIO`, `bin/Debug`, `bin/ReconfigFPGA`) and `build-info.json`; ignored by Git, since the board address is compiled in |
| `build-tools.sh` | builds the programs from the submodule with the board address from the host configuration |
| `trgctl.py` | checks a parameter file, writes it, verifies every read-back value, reads the firmware version |
| `param/` | parameter files: `scor.txt`, `scor-clock10k.txt` (`scor.txt` plus the 10 kHz clock on Clock PS, gate always open), `clock1k.txt` (register settings), `nimio.txt`, `nimio-clock10k.txt` (NIM outputs; the second puts the 10 kHz clock on NIM OUT 1 and 2) |

## Build

```bash
git submodule update --init hul/trigger/hul-trigger                 # if the repository was cloned without --recurse-submodules
cp config/hul-trigger.env.example config/local/hul-trigger.env       # then fill in the address
set -a; . config/local/hul-trigger.env; set +a
hul/trigger/build-tools.sh
```

The upstream programs take the board address from a header at compile time. The script copies `software/` of the
submodule (Makefile, `src/`, `include/`) to `hul/trigger/software/`, generates that header from `HUL_TRIGGER_IP`, and
builds there, so the submodule stays clean. It records the commit, the address, and the SHA-256 of every program in
`hul/trigger/software/build-info.json`. It refuses a submodule with local changes or at another commit than the pinned one.
Rebuild after changing the address or updating the submodule. Nothing here talks to the board.

## Use

```bash
set -a; . config/local/hul-trigger.env; set +a
hul/trigger/trgctl.py check register hul/trigger/param/scor.txt          # no board access
hul/trigger/trgctl.py version --out <new-dir>                              # reads the firmware version
hul/trigger/trgctl.py write register hul/trigger/param/scor.txt --out <new-dir>
hul/trigger/trgctl.py write nimio hul/trigger/param/nimio.txt --out <new-dir>
```

- `check` compares the file with the upstream reference in the submodule (`software/param/example.txt` or
  `software/nim_param/nimio.txt`): missing, unknown, and duplicated keys, and values outside the ranges written in its comments.
  The upstream programs stop at the first missing key after writing the registers before it, and ignore unknown keys,
  so `write` always runs `check` first and refuses a file with errors.
- `write` asks for confirmation (`--yes` skips it), runs the program in `<new-dir>` (it must not exist), and compares
  every written value with the value read back. The result is `OK`, `READBACK_MISMATCH`, or `FAILED`. `<new-dir>` keeps
  a copy of the parameter file, the program's read-back log, and `report.json` (build, address, parameter SHA-256,
  command, output, written and read-back values). A failed write may leave the board partially configured.
  `RGN3::RST_PSCNT` is a reset pulse that the program writes without reading back.
- The tools refuse to run if `HUL_TRIGGER_IP` differs from the address of the build, a program differs from the build
  record, or (for `write`) the build was made from another commit than the submodule's.
- `--software <dir>` (or `HUL_TRIGGER_SOFTWARE`) uses a build in another directory.
- The programs in `software/bin/` can also be run by hand, as in the upstream instructions, but then nothing is checked
  or recorded. Run them in a scratch directory: they write their read-back log (`last.log`, `nimio.log`) to the current directory.
- Nothing is retried. `ReconfigFPGA` is built but not wrapped.

## Tests

`../../tests/test_hul_trigger.py` checks `check` without any program. With programs built for `127.0.0.1` in a separate
directory it also writes and reads back every file in `param/` through a loopback RBCP responder (`../../tests/fake_rbcp.py`):

```bash
HUL_TRIGGER_IP=127.0.0.1 hul/trigger/build-tools.sh <dir>
HUL_TRIGGER_TEST_SOFTWARE=<dir> python3 -m unittest tests.test_hul_trigger
```
