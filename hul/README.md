# hul

Configuration and tools for the HUL boards.

- `trigger/` — trigger parameters for E88 and tools to write and read back the registers ([trigger/README.md](trigger/README.md)).
  The control programs are those of [jparc-saphire/hul-trigger](https://github.com/jparc-saphire/hul-trigger), built at a pinned commit.
- `mtx/` — 2D matrix tables, the scripts that generate them, and the upload tool.

FPGA firmware sources are not kept here. Every write is recorded with the SHA-256 of the parameter file by `trigger/trgctl.py`;
saving the configuration with each run is planned in `run-control`.


