# online-filter

Online filters of the E88 DAQ: they choose which time frames (TF) to keep. Data decoding uses `core/format` of the
online analyzer (`../online-analyzer`), and the parameters (detector definition, channel map, settings) are the common
E88 parameter files read by its `experiments/e88/Params`, so that the filter, the online analyzer and the offline
analysis see the same channels. Nothing here needs ROOT or FairMQ.

Every filter always mixes in an **unbiased prescale**: one TF out of N is kept by its position in the input, without
looking at its content, and marked as such. The prescaled TFs form an unbiased sample (for example to measure the
efficiency of the filter). There is no setting without a prescale.

## Plain prescale filter (`e88-prescale-filter`)

Keeps one TF out of N by its position in the input (index mod N = offset), without reading its content and without
parameters. Same options, outputs (`OUTPUT`, `.marks.tsv`, `.summary.json`) and FileSink form as the SC filter below;
it is the same program with `--kind prescale` as default (`e88-sc-filter --kind prescale` does the same).

```bash
build/online-filter/bin/e88-prescale-filter --input run000123.dat --output run000123_ps.dat --prescale 100
```

## SC coincidence filter (`e88-sc-filter`)

A TF is kept when one SC counter has a leading edge on the up side (side 0) and one on the down side (side 1) within
the pair window. A counter is one segment of one SC module and layer of the detector definition: SC1 and SC2 are
separate modules, so the up side of SC1 and the down side of SC2 never make a pair. All front-end modules of the TF
are used, with times relative to the TF's heartbeat frame (frame distance × 524288 ns + TDC × LSB). A pair split
between two TFs is not seen (about window / frame length of the pairs, 1e-4 for 50 ns). Payload words of the B2TT
records (`b2tt_data_type`) are not read as TDC words.

The file version reads a FileSink file and writes a FileSink file of the same form: the input header, the kept TFs
unchanged, and the input trailer. Every program that reads the input reads the output.

```bash
cmake -S online-filter -B build/online-filter && cmake --build build/online-filter -j
ctest --test-dir build/online-filter
build/online-filter/bin/e88-sc-filter --input run000123.dat --output run000123_sc.dat \
    --prescale 100 --param <e88param>/2026nov/index.txt
```

- `--prescale N` (required, N ≥ 1) and `--prescale-offset K` select the prescaled TFs: input index mod N = K.
- The window is `sc_pair_window` and the LSB `tdc_lsb_ns` of the settings; `--window` and `--tdc-lsb-ns` override them.
  `--sc-modules 0,1` limits the SC modules (default: every SC plane with two sides).
- The run number for the parameter index is taken from the FileSink header (`--run` overrides it).
- A TF that cannot be parsed is kept and counted (`malformed_kept`): the filter never drops what it cannot judge.
- Outputs are never overwritten. Next to the output:
  - `OUTPUT.marks.tsv`: one line per kept TF — input index, time frame id, output index, reasons (`sc`, `prescale`,
    `malformed`, comma separated), number of SC counters with a pair, module and segment of the first one.
    The unbiased sample is the lines with `prescale`, whatever the other reasons.
  - `OUTPUT.summary.json`: parameters used (path and SHA-256 of each file), window, prescale, and the counts
    (received, kept, `sc_pass`, `prescaled`, `prescale_only`, `malformed_kept`, SC leading edges, bytes).

## In the DAQ chain (`e88-sc-filter-device`)

The FairMQ device makes the same decision (`core/ScFilterRun`, `--kind sc` by default or `--kind prescale`) on time frames
received from a channel and sends the kept time frames unchanged to its output channel. It is built when FairMQ is found:

```bash
cmake -S online-filter -B build/online-filter -DCMAKE_PREFIX_PATH=<nestdaq prefix> && cmake --build build/online-filter -j
```

`nestdaq/scripts/run-timeframe-chain.py --sc-filter-prescale N --e88-param INDEX` (or `run-control/daqctl.py run` with the same
options) connects it to the `dqm` side output of the TimeFrameBuilder, with a second FileSink writing `runXXXXXX_sc.dat` next to the
run file; the run file is written as before. `--prescale-filter N` does the same with the plain prescale and writes `runXXXXXX_ps.dat`.
One side-output filter per session. The TimeFrameBuilder sends to `dqm` only when the filter can take the time frame and skips
it otherwise, so the filter never holds up the run file; compare `received` in the summary with the time frames of the run file.

- The run number is the NestDAQ run number (property `run_number`, as for the FileSink); the parameters are read for it at every RUN.
- Options: `--prescale N` (required), `--prescale-offset`, `--window`, `--tdc-lsb-ns`, `--sc-modules`, `--e88-param` (or
  `--e88-detector-def`, `--e88-channel-map`, `--e88-settings`), `--marks-dir` (required).
- At every STOP it writes `<marks-dir>/runXXXXXX_sc.marks.tsv` (`_ps` for the plain prescale) and `runXXXXXX_sc.summary.json` (as the file version, plus `send_failed`).
  If the parameters cannot be read for the run, it logs the error, sends nothing and writes the error in the summary.
- `--run-settings FILE` is read at every RUN (`core/RunSettings`): lines `kind sc|prescale|off` and `prescale N` replace the start
  options for that run, so the filter can be changed between runs without restarting the session. `off` keeps nothing in the run.
  A file that cannot be read is an error of that run (nothing is sent). The summary records the kind and N used (`"filter": "off"` for off).
  `--suffix S` keeps the names `runXXXXXX_S.*` for every run whatever the kind (the side-output FileSink is started with one suffix);
  the launcher gives the suffix of the session and `<output>/run-settings.txt`.

Planned: the main line (TimeFrameBuilder → filter → FileSink, only the filtered file), after the side output has been compared with
the full data in real runs; it needs the approval of the experiment's owner.
