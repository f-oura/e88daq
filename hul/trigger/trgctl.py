#!/usr/bin/env python3
"""Check, write, and read back the HUL trigger registers with the pinned hul-trigger programs.

  check   register|nimio PARAM            compare a parameter file with the upstream reference (no board access)
  version                    --out DIR    read the firmware version register (read only)
  write   register|nimio PARAM --out DIR  write a parameter file and verify every read-back value

The programs come from hul/trigger/build-tools.sh (--tools or HUL_TRIGGER_TOOLS). The board address
is compiled into them; HUL_TRIGGER_IP must match the address of the build, so a stale build is refused.

check finds missing, unknown, duplicated, and out-of-range keys. The upstream programs read keys only
when they need them and stop at the first missing one, after the registers before it have been written,
so write always runs check first and refuses a file with errors.

write and version run the program in a new evidence directory DIR (it must not exist): the parameter
file is copied there with its SHA-256, the program's read-back log lands there, and report.json records
the command, the output, every written and read-back value, and the result. write asks for confirmation
unless --yes is given. Nothing is retried. A failed write may leave the board partially configured:
the read-back log lists the registers written before the failure.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

KINDS = {
    # kind: (program, upstream reference file, read-back log written by the program)
    'register': ('Register', 'software/param/example.txt', 'last.log'),
    'nimio': ('NimIO', 'software/nim_param/nimio.txt', 'nimio.log'),
}
ANSI = re.compile(r'\x1b\[[0-9;]*m')
WR = re.compile(r'^(\S+)\s+W/R = (\d+)/(\d+)\s*$')
ERRORS = ('No such key', 'No such file', 'Something is wrong', 'Timeout', '#E')
READ_FAILED = 0xeeeeeeee
# Registers the upstream programs write without reading back (a reset pulse); read shows as 0.
WRITE_ONLY = {'RGN3::RST_PSCNT'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_param(path):
    """Return ({KEY: value}, [errors]) with the upstream reader's rules: '#' lines skipped, keys upper-cased."""
    values, errors = {}, []
    for n, line in enumerate(Path(path).read_text(errors='replace').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        fields = line.split()
        key = fields[0].upper()
        if len(fields) < 2 or not fields[1].isdigit():
            errors.append(f'line {n}: {key}: value is not a non-negative integer')
            continue
        if key in values:
            errors.append(f'line {n}: {key}: duplicated (the upstream reader keeps the last one)')
        values[key] = int(fields[1])
    return values, errors


def reference_rules(kind, path):
    """Allowed values per key, from the comments of the upstream reference file."""
    text = Path(path).read_text(errors='replace')
    keys, _ = parse_param(path)
    if kind == 'nimio':
        signals = {int(m.group(1)) for m in re.finditer(r'^\s*##\s+(\d+)\s+\S+\s+##', text, re.M)}
        return {k: (min(signals), max(signals), None) for k in keys}
    rules = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2 or line.lstrip().startswith('#') or not fields[1].isdigit():
            continue
        comment = line.split('#', 1)[1] if '#' in line else ''
        if m := re.search(r'(\d+)\s*-\s*(\d+)', comment):
            rules[fields[0].upper()] = (int(m.group(1)), int(m.group(2)), None)
        elif opts := [int(x) for x in re.findall(r'(\d+)\s*:', comment)]:
            rules[fields[0].upper()] = (None, None, set(opts))
        else:  # segment switches drawn inside a box: "Please Write 0 or 1"
            rules[fields[0].upper()] = (None, None, {0, 1})
    return rules


def check(kind, param, reference):
    values, errors = parse_param(param)
    rules = reference_rules(kind, reference)
    errors += [f'{k}: missing' for k in rules if k not in values]
    errors += [f'{k}: unknown key (the upstream programs ignore it)' for k in values if k not in rules]
    for k, v in values.items():
        lo, hi, allowed = rules.get(k, (None, None, None))
        if allowed is not None and v not in allowed:
            errors.append(f'{k} = {v}: allowed {sorted(allowed)}')
        elif lo is not None and not lo <= v <= hi:
            errors.append(f'{k} = {v}: allowed {lo}-{hi}')
    return values, errors


def load_tools(tools):
    if not tools:
        raise SystemExit('#E: give --tools or set HUL_TRIGGER_TOOLS (the work directory of build-tools.sh)')
    base = Path(tools) / 'hul-trigger'
    info_file = base / 'build-info.json'
    if not info_file.is_file():
        raise SystemExit(f'#E: {info_file} not found; run hul/trigger/build-tools.sh first')
    info = json.loads(info_file.read_text())
    return base, info


def verified_program(base, info, name):
    """The program path, after checking the configured address and the binary hash against the build."""
    ip = os.environ.get('HUL_TRIGGER_IP')
    if not ip:
        raise SystemExit('#E: HUL_TRIGGER_IP is not set (see config/hul-trigger.env.example)')
    if ip != info['board_ip']:
        raise SystemExit(f"#E: the programs were built for {info['board_ip']}, HUL_TRIGGER_IP is {ip}; rebuild")
    program = base / 'build' / 'bin' / name
    if sha256(program) != info['binaries'].get(name):
        raise SystemExit(f'#E: {program} differs from the build record; rebuild')
    return program


def new_out(out):
    out = Path(out)
    if out.exists():
        raise SystemExit(f'#E: {out} already exists')
    out.mkdir(parents=True)
    return out


def run_program(command, out, timeout):
    t0 = time.time()
    try:
        p = subprocess.run([str(c) for c in command], cwd=out, capture_output=True, text=True, timeout=timeout)
        rc, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        rc, stdout, stderr = 'TIMEOUT', exc.stdout or '', exc.stderr or ''
        stdout, stderr = [x.decode(errors='replace') if isinstance(x, bytes) else x for x in (stdout, stderr)]
    return {'command': [str(c) for c in command], 'started': t0, 'seconds': round(time.time() - t0, 3),
            'returncode': rc, 'stdout': ANSI.sub('', stdout), 'stderr': ANSI.sub('', stderr)}


def readback_log(path):
    """{label: value} from the program's read-back log (first line is the file name)."""
    if not path.exists():
        return {}
    entries = {}
    for line in path.read_text(errors='replace').splitlines()[1:]:
        fields = line.split()
        if len(fields) == 2 and fields[1].lstrip('-').isdigit():
            entries[fields[0]] = int(fields[1])
    return entries


def judge_write(run, log):
    output = run['stdout'] + run['stderr']
    writes = [{'label': m.group(1), 'written': int(m.group(2)), 'read': int(m.group(3))}
              for m in (WR.match(x.strip()) for x in output.splitlines()) if m]
    # The programs print "W/R = written/read" for every mismatch (Register: for every write). Values are
    # compared as written, since some registers take a derived value (for example a prescale minus one).
    mismatches = [w for w in writes if w['written'] != w['read'] and w['label'] not in WRITE_ONLY]
    errors = [x for x in output.splitlines() if any(e in x for e in ERRORS)]
    if run['returncode'] != 0 or errors or 'Successfully finished!' not in output:
        result = 'FAILED'
    elif mismatches or not log:
        result = 'READBACK_MISMATCH'
    else:
        result = 'OK'
    return {'result': result, 'writes': writes, 'readback': log, 'mismatches': mismatches, 'errors': errors}


def cmd_check(a):
    base, _ = load_tools(a.tools)
    _, errors = check(a.kind, a.param, base / 'src' / KINDS[a.kind][1])
    for e in errors:
        print(f'#E: {e}')
    print(f"#{'E' if errors else 'D'}: {a.param}: {len(errors)} error(s)")
    return 1 if errors else 0


def cmd_version(a):
    base, info = load_tools(a.tools)
    program = verified_program(base, info, 'Debug')
    out = new_out(a.out)
    run = run_program([program], out, a.timeout)
    text = run['stdout'].strip().splitlines()
    value = int(text[-1], 16) if text and re.fullmatch(r'[0-9a-fA-F]+', text[-1]) else None
    ok = run['returncode'] == 0 and value is not None and value != READ_FAILED and 'Timeout' not in run['stdout']
    report = {'action': 'version', 'result': 'OK' if ok else 'FAILED', 'board_ip': info['board_ip'], 'build': info,
              'version': f'0x{value:08x}' if value is not None else None, 'run': run}
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"#{'D' if ok else 'E'}: firmware version {report['version']} ({report['result']}); {out / 'report.json'}")
    return 0 if ok else 1


def cmd_write(a):
    base, info = load_tools(a.tools)
    name, reference, log_name = KINDS[a.kind]
    program = verified_program(base, info, name)
    values, errors = check(a.kind, a.param, base / 'src' / reference)
    if errors:
        for e in errors:
            print(f'#E: {e}')
        raise SystemExit(f'#E: {a.param} has {len(errors)} error(s); nothing was written')
    digest = sha256(a.param)
    if not a.yes:
        print(f'Write {a.param} (sha256 {digest[:16]}...) to the HUL trigger at {info["board_ip"]} with {name}.')
        if input('Type "write" to continue: ').strip() != 'write':
            raise SystemExit('#E: not confirmed; nothing was written')
    out = new_out(a.out)
    copy = out / Path(a.param).name
    shutil.copy2(a.param, copy)
    run = run_program([program, copy.name], out, a.timeout)
    verdict = judge_write(run, readback_log(out / log_name))
    report = {'action': f'write {a.kind}', 'result': verdict['result'], 'board_ip': info['board_ip'], 'build': info,
              'param': {'source': str(Path(a.param).resolve()), 'copy': copy.name, 'sha256': digest},
              'run': run, **{k: v for k, v in verdict.items() if k != 'result'}}
    if verdict['result'] == 'FAILED':
        report['note'] = f'the board may be partially configured; {log_name} lists the registers written before the failure'
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    for m in verdict['mismatches']:
        print(f"#E: {m['label']}: wrote {m['written']}, read back {m['read']}")
    for e in verdict['errors']:
        print(f'#E: {e}')
    print(f"#{'D' if verdict['result'] == 'OK' else 'E'}: {verdict['result']}: {len(verdict['writes'])} write(s) "
          f"reported, {len(verdict['readback'])} read back; {out / 'report.json'}")
    return 0 if verdict['result'] == 'OK' else 1


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--tools', default=os.environ.get('HUL_TRIGGER_TOOLS'), help='work directory of build-tools.sh')
    p.add_argument('--timeout', type=float, default=120, help='seconds before the program is stopped')
    sub = p.add_subparsers(dest='action', required=True)
    c = sub.add_parser('check')
    c.add_argument('kind', choices=KINDS)
    c.add_argument('param')
    v = sub.add_parser('version')
    v.add_argument('--out', required=True)
    w = sub.add_parser('write')
    w.add_argument('kind', choices=KINDS)
    w.add_argument('param')
    w.add_argument('--out', required=True)
    w.add_argument('--yes', action='store_true', help='do not ask for confirmation')
    a = p.parse_args()
    return {'check': cmd_check, 'version': cmd_version, 'write': cmd_write}[a.action](a)


if __name__ == '__main__':
    sys.exit(main())
