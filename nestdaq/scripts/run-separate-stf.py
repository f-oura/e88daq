#!/usr/bin/env python3
"""Run one NestDAQ FileSink chain per board and gate one shared HBF window from Root.

Each --source is BOARD_IP:TDC_TYPE:OUT_DIR. For every source this starts
nestdaq/scripts/run-board-chain.sh with HRTDC_TDC_TYPE set
(the chain is generic AmQStrTdcSampler -> STFBuilder -> FileSink; TdcType only
labels the STF header). Each chain has its own loopback Redis and its own id
tag, so two chains do not share IPC paths.

Order of effects:
  1. start every chain
  2. wait until every chain prints "#D: RUNNING; holding" and every sampler
     log shows "TCP connected" without "TCP connection error"; otherwise no
     write is made
  3. wait --pre seconds, set_hbfstate <root> on   (one RBCP write)
  4. wait --on seconds,  set_hbfstate <root> off  (one RBCP write; always
     attempted once on was attempted)
  5. wait for every chain to finish

At most two set_hbfstate invocations in total; no retry.

--during-on CMD (optional): run CMD once, --during-on-delay
seconds after a successful on, inside the on window. It is given the time
left before off minus one second; a timeout or a non-zero exit is recorded
and does not prevent off. Its output goes to --during-on-log. Without the
option the order of effects above is unchanged.
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time

READY_MARK = "#D: RUNNING; holding"


def sampler_tcp_state(out_dir):
    try:
        text = open(os.path.join(out_dir, "sampler.log"), errors="replace").read()
    except OSError:
        return "no_log"
    if "TCP connection error" in text:
        return "error"
    return "connected" if "TCP connected" in text else "unknown"


def run_set(tool, root, state, events):
    t0 = time.time()
    try:
        p = subprocess.run([tool, root, state], capture_output=True, text=True, timeout=10)
        rc, out = p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        rc, out = "timeout", ""
    events.append({"event": "set_hbfstate", "state": state, "wall": t0, "rc": rc, "output": out})
    return rc == 0 and "#E" not in out


def run_during_on(cmd, log_path, budget, events):
    t0 = time.time()
    with open(log_path, "w") as log:
        try:
            rc = subprocess.run(shlex.split(cmd), stdout=log, stderr=subprocess.STDOUT,
                                timeout=max(0.5, budget)).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    events.append({"event": "during_on", "cmd": cmd, "wall": t0, "seconds": round(time.time() - t0, 3),
                   "rc": rc, "log": log_path})
    return rc == 0


class Chain:
    def __init__(self, script, board, tdc_type, out, hold):
        self.board, self.tdc_type, self.out = board, tdc_type, out
        self.log_path = out.rstrip("/") + ".chain.log"
        env = dict(os.environ, HRTDC_TDC_TYPE=str(tdc_type))
        self.proc = subprocess.Popen(["bash", script, out, str(hold), board], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.ready = threading.Event()
        self.lines = []
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self):
        with open(self.log_path, "w") as log:
            for line in self.proc.stdout:
                log.write(line)
                log.flush()
                self.lines.append(line)
                if READY_MARK in line:
                    self.ready.set()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chain", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "run-board-chain.sh"))
    ap.add_argument("--source", action="append", required=True, help="BOARD_IP:TDC_TYPE:OUT_DIR")
    ap.add_argument("--root", required=True)
    ap.add_argument("--set-hbfstate", required=True)
    ap.add_argument("--pre", type=float, default=3.0)
    ap.add_argument("--on", type=float, default=10.0)
    ap.add_argument("--post", type=float, default=5.0)
    ap.add_argument("--ready-timeout", type=float, default=90.0)
    ap.add_argument("--summary", required=True)
    ap.add_argument("--during-on", help="command run once inside the on window")
    ap.add_argument("--during-on-delay", type=float, default=1.0)
    ap.add_argument("--during-on-log", help="output of --during-on (required with it)")
    args = ap.parse_args(argv)
    if args.during_on and not args.during_on_log:
        ap.error("--during-on needs --during-on-log")
    if args.during_on and not 0 <= args.during_on_delay < args.on - 1:
        ap.error("--during-on-delay must leave at least 1 s of the on window")

    hold = int(args.pre + args.on + args.post + 2.999) + 5
    events = []
    chains = []
    for spec in args.source:
        board, tdc_type, out = spec.split(":", 2)
        chains.append(Chain(args.chain, board, int(tdc_type), out, hold))
        events.append({"event": "chain_started", "board": board, "wall": time.time()})

    attempted_on = False
    on_ok = off_ok = None
    result = None
    try:
        deadline = time.time() + args.ready_timeout
        for c in chains:
            if not c.ready.wait(max(0.0, deadline - time.time())) or c.proc.poll() is not None:
                result = "STOP_CHAIN_NOT_RUNNING_" + c.board
                break
        if result is None:
            for c in chains:
                tcp = sampler_tcp_state(c.out)
                events.append({"event": "sampler_tcp", "board": c.board, "state": tcp, "wall": time.time()})
                if tcp != "connected":
                    result = "STOP_SAMPLER_TCP_%s_%s" % (tcp.upper(), c.board)
                    break
        if result is None:
            events.append({"event": "all_running", "wall": time.time()})
            time.sleep(args.pre)
            if any(c.proc.poll() is not None for c in chains):
                result = "STOP_CHAIN_EXITED_BEFORE_ON"
            else:
                attempted_on = True
                on_ok = run_set(args.set_hbfstate, args.root, "on", events)
                if on_ok and args.during_on:
                    t_on = time.time()
                    time.sleep(args.during_on_delay)
                    run_during_on(args.during_on, args.during_on_log,
                                  args.on - args.during_on_delay - 1.0, events)
                    time.sleep(max(0.0, args.on - (time.time() - t_on)))
                elif on_ok:
                    time.sleep(args.on)
    finally:
        if attempted_on:
            off_ok = run_set(args.set_hbfstate, args.root, "off", events)
    rcs = {}
    for c in chains:
        rcs[c.board] = c.proc.wait(timeout=hold + 120)
        c.thread.join(timeout=5)
        events.append({"event": "chain_exited", "board": c.board, "rc": rcs[c.board], "wall": time.time()})
    if result is None:
        result = "COMPLETED" if (on_ok and off_ok and all(v == 0 for v in rcs.values())) else "STOP_ERROR"

    summary = {"result": result, "root": args.root, "chain_hold_s": hold,
               "windows_s": {"pre": args.pre, "on": args.on, "post": args.post},
               "sources": [{"board": c.board, "tdc_type": c.tdc_type, "out": c.out, "chain_log": c.log_path,
                            "chain_rc": rcs.get(c.board), "chain_tail": [l.rstrip() for l in c.lines[-6:]]} for c in chains],
               "set_hbfstate_invocations": sum(1 for e in events if e["event"] == "set_hbfstate"),
               "on_ok": on_ok, "off_ok": off_ok,
               "during_on_rc": next((e["rc"] for e in events if e["event"] == "during_on"), None),
               "events": events}
    with open(args.summary, "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))
    return 0 if result == "COMPLETED" else 4


if __name__ == "__main__":
    sys.exit(main())
