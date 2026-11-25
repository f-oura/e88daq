#!/usr/bin/env python3
"""Manifest-driven multi-board status, conditional HR-TDC preparation and HBF run."""
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'nestdaq/lib'))
from run_storage import output_path


def invoke(command, events, timeout=10):
    event = {'time': time.time(), 'command': list(map(str, command))}
    events.append(event)
    try:
        p = subprocess.run(event['command'], capture_output=True, text=True, timeout=timeout)
        event.update(rc=p.returncode, output=p.stdout + p.stderr)
    except Exception as exc:
        event.update(rc=None, error=str(exc))
        raise
    if p.returncode or '#E' in event['output']:
        raise RuntimeError('command failed: ' + event['output'])
    return event['output']


def read(reader, ip, address, width, events):
    text = invoke([reader, ip, '%08x' % address, str(width)], events)
    values = re.findall(r'\(0x([0-9a-fA-F]+)\)', text)
    if len(values) != 1 or 'eeeeeeee' in text.lower():
        raise RuntimeError('unusable register response: ' + text)
    return int(values[0], 16)


def inspect(board, reader, events):
    ip = board['endpoint']
    route = invoke(['ip', 'route', 'get', ip], events)
    if ('dev ' + board['interface']) not in route or ('src ' + board['source']) not in route:
        raise RuntimeError('route/source mismatch for ' + ip)
    neighbor = invoke(['ip', 'neigh', 'show', ip], events)
    if board['mac'].lower() not in neighbor.lower() or any(s in neighbor for s in ['FAILED', 'INCOMPLETE']):
        raise RuntimeError('neighbor identity unavailable for ' + ip)
    identity = read(reader, ip, board['identity_address'], 4, events)
    if identity != board['identity_value']:
        raise RuntimeError('firmware identity mismatch for ' + ip)
    row = {'endpoint': ip, 'serial': board['serial'], 'identity': identity}
    row['links'] = [read(reader, ip, address, 4, events) for address in [0, 0x00500000, 0x00600000]]
    mask = board.get('link_mask')
    # An unknown mask is reported, never assumed: the board counts as not linked.
    row['linked'] = mask is not None and all(v & mask == mask for v in row['links'])
    if board['role'] == 'hrtdc':
        row['control'] = read(reader, ip, 0x20300000, 1, events)
        row['ddr_status'] = read(reader, ip, 0x20400000, 1, events)
        row['ready'] = row['linked'] and row['control'] == 12 and row['ddr_status'] == 3
    else:
        row['ready'] = row['linked']
    return row


def capture_with_receiver_output(reader, writer, receiver_ip, root_ip, command, events):
    """Real B2TT only: enable bit 2, verify, capture, confirm HBF off, restore."""
    address = 0x30100000
    original = read(reader, receiver_ip, address, 1, events)
    if original not in (0, 4):
        raise RuntimeError('Receiver control must be real-source idle/output-only (0 or 4)')
    changed = original == 0
    if changed:
        invoke([writer, receiver_ip, '%08x' % address, '04', '1'], events)
    if read(reader, receiver_ip, address, 1, events) != 4:
        raise RuntimeError('Receiver output enable readback mismatch; stop without repeated writes')
    try:
        invoke(command, events, timeout=None)
    finally:
        if read(reader, root_ip, 0x00b00000, 1, events) != 0:
            raise RuntimeError('Root HBF state uncertain; stop further writes')
        if changed:
            invoke([writer, receiver_ip, '%08x' % address, '00', '1'], events)
        if read(reader, receiver_ip, address, 1, events) != original:
            raise RuntimeError('Receiver control restoration mismatch; stop without retry')


def capture_outcome(out, data_file, webctl, no_file=False):
    """Result of a timeframe run from what it left behind, not only from the process exit codes.

    The data file of the run number given at the start and the RUN hooks of the web controller tell whether
    data can exist: a session ended without any RUN, or a fixed capture that wrote no file, has no data.
    A session started with --no-file writes no file on purpose: its result is NO_FILE.
    """
    if no_file:
        info = {'result': 'NO_FILE', 'data_file_exists': False}
        if webctl:
            path = out / 'timeframe' / 'hooks.jsonl'
            hooks = [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
            info['runs_started'] = sum(1 for h in hooks if h.get('event') == 'post-run' and h.get('status') == 'OK')
        return info
    info = {'data_file_exists': data_file.exists()}
    if info['data_file_exists']:
        info['data_file_bytes'] = data_file.stat().st_size
    if webctl:
        # The hooks append to hooks.jsonl at every RUN/STOP; read it directly, since the chain report lacks the
        # hooks when the chain itself ended with an error.
        path = out / 'timeframe' / 'hooks.jsonl'
        hooks = [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
        info['runs_started'] = sum(1 for h in hooks if h.get('event') == 'post-run' and h.get('status') == 'OK')
    if info['data_file_exists'] and info['data_file_bytes'] > 0:
        info['result'] = 'CAPTURE_COMPLETED_REQUIRES_DECODE'
    elif webctl and info['runs_started'] > 0:
        # RUN was pressed; the run number may have been changed in the page, so the data is under another name.
        info['result'] = 'CAPTURE_COMPLETED_REQUIRES_DECODE'
        info['note'] = 'no data file for the starting run number; the run number was changed in the web controller?'
    else:
        info['result'] = 'NO_DATA'
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action', choices=['status', 'prepare', 'run'])
    ap.add_argument('--manifest', default=str(ROOT / 'config/local/setup.yaml'))
    ap.add_argument('--reader', required=True)
    ap.add_argument('--initialize')
    ap.add_argument('--writer', help='pinned hul-common-lib write_register; required for run')
    ap.add_argument('--set-hbfstate')
    ap.add_argument('--out', required=True, help='new evidence directory; never reused')
    ap.add_argument('--seconds', type=float, default=10)
    ap.add_argument('--run-number', type=int, help='required for default timeframe run; explicit E88 number')
    ap.add_argument('--data-dir', help='TF data directory; default from setup manifest')
    ap.add_argument('--webctl', metavar='ADDR:PORT', help='timeframe run controlled from daq-webctl at this address instead of a fixed --seconds capture')
    ap.add_argument('--builder', choices=['timeframe','separate-stf'], default='timeframe', help='run output topology (default: TimeFrameBuilder over every data source)')
    ap.add_argument('--sc-filter-prescale', type=int, metavar='N', help='timeframe run: also write run<NNNNNN>_sc.dat with the SC coincidence filter on the side output; N: unbiased prescale (>= 1)')
    ap.add_argument('--e88-param', help='E88 parameter index for the SC filter (required with --sc-filter-prescale) and the online analyzer')
    ap.add_argument('--prescale-filter', type=int, metavar='N', help='timeframe run: also write run<NNNNNN>_ps.dat with 1 of N time frames (plain prescale on the side output)')
    ap.add_argument('--analyzer-http', metavar='ADDR:PORT', help='timeframe run: run the online analyzer on the run file and serve its histograms here')
    ap.add_argument('--no-file', action='store_true', help='timeframe run: write no run file (side-output filters are skipped; the online analyzer reads the data stream)')
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    events, result = [], {'action': args.action, 'boards': {}, 'result': 'FAIL_STOP'}
    try:
        manifest = yaml.safe_load(pathlib.Path(args.manifest).read_text())
        config = manifest['operational_control']
        boards = config['boards']
        for name, board in boards.items():
            binding = manifest['timing_root'] if board['role'] == 'root' else manifest['data_sources'][name]
            board.update(endpoint=binding['endpoint'], serial=binding['serial'])
        result['manifest_sha256'] = hashlib.sha256(pathlib.Path(args.manifest).read_bytes()).hexdigest()
        if args.seconds <= 0:
            raise ValueError('seconds must be positive')
        if args.action == 'prepare' and not args.initialize:
            raise ValueError('--initialize is required for prepare')
        if args.action == 'run' and not args.writer:
            raise ValueError('--writer is required for Receiver output control during run')
        if args.action == 'run' and not args.set_hbfstate:
            raise ValueError('--set-hbfstate is required for run')
        if args.action == 'run' and args.builder == 'timeframe':
            if args.run_number is None:raise ValueError('--run-number is required for timeframe run')
            data_file=output_path(args.data_dir or manifest['shared_timeframe_topology']['storage']['directory'],args.run_number)
            result.update(run_number=args.run_number,data_file=str(data_file))
        elif args.action == 'run' and (args.run_number is not None or args.data_dir is not None):
            raise ValueError('run-number/data-dir options apply to timeframe mode only')
        if args.webctl and (args.action != 'run' or args.builder != 'timeframe'):
            raise ValueError('--webctl applies to the timeframe run only')
        if args.analyzer_http and (args.action != 'run' or args.builder != 'timeframe'):
            raise ValueError('--analyzer-http applies to the timeframe run only')
        if args.analyzer_http:
            result['online_analyzer'] = {'http': args.analyzer_http, 'e88_param': args.e88_param}
        if args.no_file and (args.action != 'run' or args.builder != 'timeframe'):
            raise ValueError('--no-file applies to the timeframe run only')
        result['no_file'] = args.no_file
        if args.sc_filter_prescale is not None or args.prescale_filter is not None:
            if args.action != 'run' or args.builder != 'timeframe':
                raise ValueError('side-output filters apply to the timeframe run only')
            if args.sc_filter_prescale is not None and args.prescale_filter is not None:
                raise ValueError('one side-output filter per session: --sc-filter-prescale or --prescale-filter')
        if args.sc_filter_prescale is not None:
            if args.sc_filter_prescale < 1 or not args.e88_param:
                raise ValueError('--sc-filter-prescale needs N >= 1 and --e88-param')
            result['side_filter'] = {'kind': 'sc', 'prescale': args.sc_filter_prescale, 'e88_param': args.e88_param}
        if args.prescale_filter is not None:
            if args.prescale_filter < 1:
                raise ValueError('--prescale-filter needs N >= 1')
            result['side_filter'] = {'kind': 'prescale', 'prescale': args.prescale_filter}
        for name, board in boards.items():
            result['boards'][name] = inspect(board, args.reader, events)
        root = next(b for b in boards.values() if b['role'] == 'root')
        if read(args.reader, root['endpoint'], 0x00b00000, 1, events) != 0:
            raise RuntimeError('Root HBF must be off before preparation/run')
        if not all(row['linked'] for row in result['boards'].values()):
            raise RuntimeError('one or more board links are down')
        if args.action == 'prepare':
            for name, board in boards.items():
                if board['role'] != 'hrtdc' or result['boards'][name]['ready']:
                    continue
                text = invoke([args.initialize, board['endpoint'], 'both'], events, timeout=120)
                if not all(mark in text for mark in ['DDR initialize succeeded (MZN-U)', 'DDR initialize succeeded (MZN-D)', 'LUT is ready! (MIF-U)', 'LUT is ready! (MIF-D)']):
                    raise RuntimeError('initialization success markers missing')
                result['boards'][name] = inspect(board, args.reader, events)
        if not all(row['ready'] for row in result['boards'].values()):
            raise RuntimeError('not all boards READY; prepare required')
        if args.action == 'run':
            if args.builder == 'timeframe':
                unchecked = set(manifest['data_sources']) - set(boards)
                if unchecked:
                    raise RuntimeError('data sources without a board check: ' + ', '.join(sorted(unchecked)))
                cmd = [sys.executable, str(ROOT/'nestdaq/scripts/run-timeframe-chain.py'), str(out/'timeframe'), '--live', '--seconds', str(args.seconds), '--manifest', str(pathlib.Path(args.manifest).resolve()), '--set-hbfstate', args.set_hbfstate, '--run-number',str(args.run_number),'--data-dir',str(data_file.parent)]
                if args.webctl:
                    cmd += ['--webctl', args.webctl]
                if args.sc_filter_prescale is not None:
                    cmd += ['--sc-filter-prescale', str(args.sc_filter_prescale), '--e88-param', args.e88_param]
                if args.prescale_filter is not None:
                    cmd += ['--prescale-filter', str(args.prescale_filter)]
                if args.analyzer_http:
                    cmd += ['--analyzer-http', args.analyzer_http]
                    if args.e88_param and args.sc_filter_prescale is None:
                        cmd += ['--e88-param', args.e88_param]
                if args.no_file:
                    cmd += ['--no-file']
            else:
                cmd = [sys.executable, str(ROOT/'nestdaq/scripts/run-separate-stf.py'), '--root', root['endpoint'], '--set-hbfstate', args.set_hbfstate, '--on', str(args.seconds), '--summary', str(out/'capture.json')]
                for name, board in boards.items():
                    if board['role'] != 'root':
                        cmd += ['--source', '%s:%s:%s' % (board['endpoint'], board['tdc_type'], out/name)]
            result['builder'] = args.builder
            try:
                # Existing runner guarantees off after any on attempt. Do not time it out here.
                receivers = [b for b in boards.values() if b['role'] == 'receiver']
                if len(receivers) != 1:
                    raise RuntimeError('run currently requires exactly one Receiver')
                capture_with_receiver_output(args.reader, args.writer, receivers[0]['endpoint'], root['endpoint'], cmd, events)
            finally:
                result['root_hbf_post'] = read(args.reader, root['endpoint'], 0x00b00000, 1, events)
            if result['root_hbf_post'] != 0:
                raise RuntimeError('Root HBF was not restored off')
        if args.action != 'run':
            result['result'] = 'READY'
        elif args.builder == 'timeframe':
            result.update(capture_outcome(out, data_file, args.webctl, args.no_file))
        else:
            result['result'] = 'CAPTURE_COMPLETED_REQUIRES_DECODE'
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        result['events'] = events
        (out/'report.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k:v for k,v in result.items() if k != 'events'}, indent=2))
    return 0 if result['result'] != 'FAIL_STOP' else 2

if __name__ == '__main__':
    sys.exit(main())
