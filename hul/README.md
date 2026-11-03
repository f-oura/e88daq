# hul

Configuration and tools for the HUL boards.

- `trigger/` — trigger parameters for E88 and tools to write and read back the registers.
  The control software is [jparc-saphire/hul-trigger](https://github.com/jparc-saphire/hul-trigger).
- `mtx/` — 2D matrix tables, the scripts that generate them, and the upload tool.

FPGA firmware sources are not kept here. Every configuration used in a run is saved with its SHA-256 by `run-control`.


