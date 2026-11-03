# amaneq

Tools and settings for the AMANEQ boards (MIKUMARI root, Str-HRTDC, B2TT trigger receiver).

## Control tools

`build-control-tools.sh <work-dir>` builds, at the commits pinned in `../manifests/upstream.yaml`:

- [hul-common-lib](https://github.com/spadi-alliance/hul-common-lib): `read_register`, `write_register`, `set_hbfstate`, ...
  (`<work-dir>/hul-common-lib/install/bin/`)
- [amaneq-soft](https://github.com/spadi-alliance/amaneq-soft): Str-HRTDC tools, including `initialize`
  (DDR alignment and LUT calibration of both mezzanines; `<work-dir>/amaneq-soft/install/StrHRTDC/bin/`)

The script prints the SHA-256 of every installed tool. Builds are host-specific, so record the hashes of the build that is used on the DAQ host.

## Board settings

Channel masks, thresholds, pairing bypass, and ToT filter settings, and tools to read them back, will be added here.
Firmware is not kept in this repository.
