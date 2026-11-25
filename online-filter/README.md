# online-filter

Online filters of the E88 DAQ: they choose which time frames (TF) to keep. Data decoding uses `core/format` of the
online analyzer (`../online-analyzer`), and the parameters (detector definition, channel map, settings) are the common
E88 parameter files read by its `experiments/e88/Params`, so that the filter, the online analyzer and the offline
analysis see the same channels. Nothing here needs ROOT or FairMQ.

Every filter always mixes in an **unbiased prescale**: one TF out of N is kept by its position in the input, without
looking at its content, and marked as such. The prescaled TFs form an unbiased sample (for example to measure the
efficiency of the filter). There is no setting without a prescale.

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

Planned: a FairMQ device with the same decision for the side output (dqm) of the TimeFrameBuilder, and later the
main line (TimeFrameBuilder → filter → FileSink), which needs the approval of the experiment's owner.
