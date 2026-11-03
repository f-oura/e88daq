# tools

Offline checks of captured data. None of them contacts a board.

- `decode_amq_strtdc.py` — counts AMANEQ StrTdc words (data, heartbeat delimiters, trailers, throttling) per channel
  in a raw SiTCP capture or a FileSink file.
- `compare_hbframes.py` — compares the heartbeat-frame numbers of two captures (missing, duplicated, offset).
- `demux_timeframe.py` — checks a TimeFrameBuilder output (each TF has every FEM of the setup once, matching IDs, no duplicates,
  FileSink trailer) and writes the unchanged STF stream of each source to `<role>.stf`.
- `replay_timeframe_inputs.py` — extracts STF messages from saved per-board files as replay input for `nestdaq/scripts/run-timeframe-chain.py --replay`.
- `verify_timeframe_replay.py` — checks a replay output against those inputs byte by byte.
