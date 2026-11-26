# e88daq

DAQ software and configuration for the J-PARC E88 streaming DAQ.
This repository is cloned on the DAQ hosts, and production runs are taken with the processes and configuration in it.

The DAQ is built on [NestDAQ](https://github.com/spadi-alliance/nestdaq) and
[nestdaq-user-impl](https://github.com/spadi-alliance/nestdaq-user-impl).
Front-end boards are AMANEQ (Str-HRTDC and a B2TT trigger receiver) and HUL (trigger logic and 2D matrix).

## What is in this repository

This repository holds what runs on the DAQ hosts and its configuration.
Source code that is developed elsewhere (FPGA firmware, NestDAQ itself, the online analyzer)
is not copied here; its exact version is pinned in `manifests/` or as a submodule,
so that one commit of this repository identifies everything used in a run.

```
e88daq/
├── nestdaq/
│   ├── scripts/       fetch and build pinned NestDAQ; run the DAQ chain
│   ├── devices/       E88 FairMQ devices
│   ├── webctl/        controller page served by daq-webctl (based on the NestDAQ page)
│   ├── lib/           Python helpers shared by the scripts (TF/STF format, run file names)
│   └── topology/      device chains for other board setups
├── run-control/       board checks, HR-TDC preparation, and runs (daqctl.py)
├── amaneq/            AMANEQ control tools (pinned build) and board settings
├── hul/
│   ├── trigger/       HUL trigger parameters and register tools
│   └── mtx/           2D matrix tables, table generation, and upload tools
├── online-filter/     online filters: SC up/down coincidence with an unbiased prescale, plain prescale (file versions, FairMQ device)
├── online-analyzer/   online analyzer (submodule, pinned)
├── tools/             offline checks of captured data
├── tests/             unit tests (no board needed)
├── manifests/         pinned versions of upstream software
└── config/            configuration templates (*.example)
```

## Setup

```bash
git clone --recurse-submodules <repository URL> e88daq
```

Then, on the DAQ host:

1. Copy `config/local.env.example`, `config/setup.yaml.example`, and `config/hul-trigger.env.example` to `config/local/` and fill them in.
2. Build NestDAQ and the devices: [nestdaq/README.md](nestdaq/README.md).
3. Build the board control tools: [amaneq/README.md](amaneq/README.md) and [hul/trigger/README.md](hul/trigger/README.md).
4. Check the boards and take runs: [run-control/README.md](run-control/README.md).
   Build the online analyzer and filters if they are used ([nestdaq/README.md](nestdaq/README.md), [online-filter/README.md](online-filter/README.md)).
5. Check the data: [tools/README.md](tools/README.md).

## Host-specific configuration

Do not commit IP addresses, MAC addresses, host names, ports, passwords, keys, or data files.
Copy a template from `config/*.example` to `config/local/` (ignored by Git) and edit it there.

## Status

Under construction. The TimeFrameBuilder chain takes any number of data sources from the setup file. It has been tested by replaying saved data
(the B2TT receiver and one Str-HRTDC board, and an eight-source chain); live operation with these scripts is not tested yet.
The online analyzer runs with a session (on the run file, or on the data stream when no file is written) and the online filters
run on the TimeFrameBuilder side output; both have been tested by replaying saved data. The HUL trigger tools write and read back the registers; they have been tested against a loopback responder and on the board, where every read-back value matched.
The 2D matrix tools and run lists are being added.
