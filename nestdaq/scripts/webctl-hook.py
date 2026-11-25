#!/usr/bin/env python3
"""Run-control hook called by daq-webctl around RUN and STOP (see run-timeframe-chain.py --webctl).

pre-run   remember how many TCP connections every sampler has logged so far
post-run  wait until every sampler logs a new TCP connection, then switch the root HBF on
pre-stop  switch the root HBF off before STOP is published
post-stop record only

Without --set-hbfstate (replay) the hook only records. Every call appends one JSON line
to <output>/hooks.jsonl. A failed HBF switch is recorded and never retried.
With --redis-port (the session Redis) the post-run record also holds the run number and the run comment
(run_info:run_number, run_info:run_comment), so every RUN of the session is listed with its comment.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import redis_lite  # noqa: E402


def run_info(port, record):
    """Run number and comment of the session Redis; a failure is recorded, never raised."""
    try:
        number = redis_lite.command(port, 'GET', 'run_info:run_number')
        comment = redis_lite.command(port, 'GET', 'run_info:run_comment')
        record['run_number'] = int(number) if number is not None else None
        record['run_comment'] = comment.decode(errors='replace') if comment is not None else ''
    except Exception as exc:  # noqa: BLE001 - the run must go on
        record['run_info_error'] = str(exc)


def tcp_counts(output, inputs):
    counts = {}
    for name in inputs:
        log = output / f'{name}.log'
        text = log.read_text(errors='replace') if log.exists() else ''
        counts[name] = {'connected': text.count('TCP connected'),
                        'failed': text.count('TCP connection error') + text.count('Socket close')}
    return counts


def set_hbf(tool, root, state, record):
    result = subprocess.run([tool, root, state], capture_output=True, text=True, timeout=10)
    record.setdefault('hbf', []).append({'state': state, 'rc': result.returncode, 'output': result.stdout + result.stderr})
    if result.returncode or '#E' in result.stdout + result.stderr:
        raise RuntimeError(f'HBF {state} failed; not retried')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('event', choices=['pre-run', 'post-run', 'pre-stop', 'post-stop'])
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--inputs', nargs='*', default=[], help='sampler service names (log file stems)')
    p.add_argument('--set-hbfstate')
    p.add_argument('--root')
    p.add_argument('--tcp-timeout', type=float, default=15)
    p.add_argument('--redis-port', type=int, help='session Redis on 127.0.0.1: record the run number and comment at post-run')
    a = p.parse_args()
    state_file = a.output / 'hook-state.json'
    record = {'event': a.event, 'time': time.time()}
    try:
        if a.event == 'pre-run':
            record['tcp_before'] = tcp_counts(a.output, a.inputs)
            state_file.write_text(json.dumps(record['tcp_before']))
        elif a.event == 'post-run' and a.set_hbfstate:
            before = json.loads(state_file.read_text()) if state_file.exists() else {}
            deadline = time.monotonic() + a.tcp_timeout
            while True:
                now = tcp_counts(a.output, a.inputs)
                failed = [n for n in a.inputs if now[n]['failed'] > before.get(n, {}).get('failed', 0)]
                if failed:
                    raise RuntimeError(f'sampler TCP failed: {failed}; HBF stays off')
                waiting = [n for n in a.inputs if now[n]['connected'] <= before.get(n, {}).get('connected', 0)]
                if not waiting:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'sampler TCP not connected: {waiting}; HBF stays off')
                time.sleep(0.1)
            set_hbf(a.set_hbfstate, a.root, 'on', record)
        elif a.event == 'pre-stop' and a.set_hbfstate:
            set_hbf(a.set_hbfstate, a.root, 'off', record)
            time.sleep(1)
        record['status'] = 'OK'
    except Exception as exc:
        record['status'] = 'FAILED'
        record['error'] = str(exc)
    if a.event == 'post-run' and a.redis_port:
        run_info(a.redis_port, record)
    with open(a.output / 'hooks.jsonl', 'a') as f:
        f.write(json.dumps(record) + '\n')
    print(f"#{'D' if record['status'] == 'OK' else 'E'}: webctl hook {a.event}: {record.get('error', 'ok')}", flush=True)
    return 0 if record['status'] == 'OK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
