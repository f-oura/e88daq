#!/usr/bin/env python3
"""Small web page that starts a browser-controlled DAQ session (daqctl.py run --webctl).

Serve it on the loopback address behind the same password-protected proxy as daq-webctl.
The page shows whether a session is running, the result of the last one, and a form to
start a new one with an explicit run number. Before starting it checks that no session or
other board-touching process is running and that the run file does not exist yet.

Configuration (a bash file, see config/session.env.example):
  SESSION_COMMAND   command run through bash; {run_number} and {out} are substituted. When the page asks for
                    no data file, {no_file} becomes --no-file (without {no_file}, --no-file is appended)
  SESSION_OUT_BASE  directory for one evidence directory per session
  SESSION_DATA_DIR  directory of runXXXXXX.dat files
  SESSION_WEBCTL    host:port where the session's daq-webctl listens
  SESSION_PREPARE_COMMAND  (optional) command run through bash by the Prepare boards button; {out} is substituted.
                    Usually daqctl.py prepare, which initializes only the boards that are not READY

For the controller page of the running session (nestdaq/webctl/daq-webctl.html, served under the same proxy):
  GET  recording.json         whether the session writes run files, and the newest runNNNNNN.dat it wrote (name, size)
  GET  run-settings.json      settings of the side-output filter for the next RUN
  POST run-settings           kind (sc, prescale, off) and prescale (N) for the next RUN
  GET  run-comment.json       the run comment (run_info:run_comment in the session Redis)
  POST run-comment            set the run comment; the FileSink writes it into the header and trailer of the run file
                              at the next RUN, and the run hook records it in hooks.jsonl
"""
import argparse
import html
import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nestdaq/lib'))
import redis_lite  # noqa: E402
import run_settings  # noqa: E402

COMMENT_KEY = 'run_info:run_comment'
COMMENT_BYTES = 255  # the FileSink keeps 255 bytes (header and trailer field of 256 bytes)


def clean_comment(text):
    """One line of at most 255 bytes (UTF-8); line breaks and tabs become spaces, other control characters are refused."""
    text = ' '.join(text.replace('\t', ' ').splitlines()).strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError('the comment contains control characters')
    if len(text.encode()) > COMMENT_BYTES:
        raise ValueError(f'the comment is longer than {COMMENT_BYTES} bytes ({len(text.encode())})')
    return text
BUSY = re.compile(r'(daqctl\.py|run-timeframe-chain\.py|run-board-chain\.sh|run-separate-stf\.py|AmQStrTdcSampler|'
                  r'/write_register|/set_hbfstate|/initialize|daq-webctl)(\s|$)')


def load_config(path):
    out = subprocess.run(['bash', '-c', 'set -a; . "$1"; env -0', 'bash', str(path)], capture_output=True, check=True)
    env = dict(x.split('=', 1) for x in out.stdout.decode().split('\0') if '=' in x)
    for key in ('SESSION_COMMAND', 'SESSION_OUT_BASE', 'SESSION_DATA_DIR', 'SESSION_WEBCTL'):
        if not env.get(key):
            raise SystemExit(f'{key} missing in {path}')
    return env


def busy_processes(own=()):
    found = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) in own:
            continue
        try:
            cmd = Path(f'/proc/{pid}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
        except OSError:
            continue
        if BUSY.search(cmd) and 'session-server.py' not in cmd:
            found.append(f'{pid}: {cmd[:160]}')
    return found


def listening(hostport):
    host, port = hostport.rsplit(':', 1)
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except OSError:
        return False


def session_command(template, run_number, out, no_file=False):
    command = template.replace('{run_number}', str(run_number)).replace('{out}', shlex.quote(str(out)))
    flag = '--no-file' if no_file else ''
    if '{no_file}' in command:
        return command.replace('{no_file}', flag)
    return command + (' ' + flag if flag else '')


class Sessions:
    def __init__(self, env, config_path):
        self.env, self.config_path = env, config_path
        self.base = Path(env['SESSION_OUT_BASE'])
        self.data = Path(env['SESSION_DATA_DIR'])
        self.base.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.proc = None
        self.current = self.base / 'current.json'
        self.prepare_file = self.base / 'prepare.json'

    def state(self):
        info = json.loads(self.current.read_text()) if self.current.exists() else {}
        alive = bool(info.get('pid')) and info.get('ended') is None and Path(f"/proc/{info['pid']}").exists()
        if info and not alive and info.get('ended') is None:
            info['ended'] = time.time()  # ended while the server was not watching
            self.current.write_text(json.dumps(info))
        if info.get('ended') is not None and 'report' not in info:
            report = Path(info['out']) / 'report.json'
            if report.exists():
                r = json.loads(report.read_text())
                info['report'] = {k: r.get(k) for k in ('result', 'error', 'root_hbf_post', 'run_number', 'data_file', 'runs_started', 'note', 'no_file')}
                self.current.write_text(json.dumps(info))
        info['running'] = alive
        info['controller_up'] = alive and listening(self.env['SESSION_WEBCTL'])
        return info

    def prepare_command(self):
        return self.env.get('SESSION_PREPARE_COMMAND', '')

    def prepare_state(self):
        """The last Prepare boards run: running, result, and the boards that are not READY."""
        info = json.loads(self.prepare_file.read_text()) if self.prepare_file.exists() else {}
        alive = bool(info.get('pid')) and info.get('ended') is None and Path(f"/proc/{info['pid']}").exists()
        if info and not alive and info.get('ended') is None:
            info['ended'] = time.time()
            self.prepare_file.write_text(json.dumps(info))
        if info.get('ended') is not None and 'report' not in info:
            report = Path(info['out']) / 'report.json'
            if report.exists():
                r = json.loads(report.read_text())
                boards = r.get('boards') or {}
                info['report'] = {'result': r.get('result'), 'error': r.get('error'),
                                  'not_ready': [name for name, b in boards.items() if not b.get('ready')],
                                  'boards': len(boards)}
                self.prepare_file.write_text(json.dumps(info))
        info['running'] = alive
        return info

    def prepare(self):
        with self.lock:
            if not self.prepare_command():
                raise ValueError('SESSION_PREPARE_COMMAND is not set')
            if self.state().get('running'):
                raise ValueError('a session is running; end it first')
            if self.prepare_state().get('running'):
                raise ValueError('the boards are already being prepared')
            busy = busy_processes()
            if busy:
                raise ValueError('other DAQ or board processes are running: ' + '; '.join(busy))
            out = self.base / time.strftime('%Y%m%d-%H%M%S-prepare')
            command = self.prepare_command().replace('{out}', shlex.quote(str(out)))
            log = open(self.base / f'{out.name}.log', 'w')
            proc = subprocess.Popen(['bash', '-c', command], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, env={**os.environ, **self.env})
            info = {'pid': proc.pid, 'out': str(out), 'log': log.name, 'command': command, 'started': time.time(),
                    'ended': None}
            self.prepare_file.write_text(json.dumps(info))
            threading.Thread(target=self._watch, args=(proc, log, self.prepare_file), daemon=True).start()
            with open(self.base / 'sessions.jsonl', 'a') as f:
                f.write(json.dumps({'action': 'prepare', 'out': str(out), 'started': info['started'],
                                    'command': command}) + '\n')
        return proc

    def next_run_number(self):
        numbers = [int(m.group(1)) for p in self.data.glob('run*.dat') if (m := re.fullmatch(r'run(\d{6})\.dat', p.name))]
        return max(numbers, default=0) + 1

    def start(self, run_number, no_file=False):
        with self.lock:
            if self.state().get('running'):
                raise ValueError('a session is already running')
            if self.prepare_state().get('running'):
                raise ValueError('the boards are being prepared; wait until it finishes')
            if not 0 < run_number <= 999999:
                raise ValueError('run number must be 1..999999')
            if (self.data / f'run{run_number:06d}.dat').exists():
                raise ValueError(f'run{run_number:06d}.dat already exists')
            if listening(self.env['SESSION_WEBCTL']):
                raise ValueError(f"{self.env['SESSION_WEBCTL']} is already in use")
            busy = busy_processes()
            if busy:
                raise ValueError('other DAQ or board processes are running: ' + '; '.join(busy))
            out = self.base / time.strftime(f'%Y%m%d-%H%M%S-run{run_number:06d}')
            command = session_command(self.env['SESSION_COMMAND'], run_number, out, no_file)
            log = open(self.base / f'{out.name}.log', 'w')
            self.proc = subprocess.Popen(['bash', '-c', command], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, env={**os.environ, **self.env})
            info = {'pid': self.proc.pid, 'run_number': run_number, 'out': str(out), 'log': log.name,
                    'command': command, 'started': time.time(), 'ended': None, 'no_file': no_file}
            self.current.write_text(json.dumps(info))
            threading.Thread(target=self._watch, args=(self.proc, log, self.current), daemon=True).start()
            with open(self.base / 'sessions.jsonl', 'a') as f:
                f.write(json.dumps({k: info[k] for k in ('run_number', 'out', 'started', 'command', 'no_file')}) + '\n')

    def recording(self):
        """Whether the running session writes run files, and the newest runNNNNNN.dat written since it started."""
        s = self.state()
        info = {'session_running': bool(s.get('running')), 'no_file': bool(s.get('no_file'))}
        if not info['session_running'] or info['no_file']:
            return info
        newest = None
        for path in self.data.glob('run*.dat'):
            if not re.fullmatch(r'run\d{6}\.dat', path.name):
                continue  # not the side-output files (run<NNNNNN>_sc.dat, ...)
            try:
                st = path.stat()
            except OSError:
                continue
            # File times come from the kernel's coarse clock and can be a little behind time.time(): allow 2 s.
            if st.st_mtime >= s.get('started', 0) - 2 and (newest is None or st.st_mtime > newest[1].st_mtime):
                newest = (path, st)
        info['exists'] = newest is not None
        if newest:
            path, st = newest
            info.update(file=path.name, run_number=int(path.name[3:9]), bytes=st.st_size,
                        seconds_since_write=max(0.0, time.time() - st.st_mtime))
        return info

    def settings_path(self):
        s = self.state()
        if not s.get('running') or not s.get('out'):
            return None
        path = Path(s['out']) / 'timeframe' / 'run-settings.txt'
        return path if path.exists() else None

    def get_settings(self):
        path = self.settings_path()
        if path is None:
            s = self.state()
            if not s.get('running'):
                reason = 'no session is running'
            elif s.get('no_file'):
                reason = 'the side-output filters do not run in a session that writes no run files'
            else:
                reason = 'this session runs no side-output filter (it starts with --sc-filter-prescale or --prescale-filter)'
            return {'available': False, 'reason': reason}
        try:
            return {'available': True, **run_settings.read(path)}
        except (OSError, ValueError) as exc:
            return {'available': True, 'error': str(exc)}

    def set_settings(self, kind, prescale):
        with self.lock:
            path = self.settings_path()
            if path is None:
                raise ValueError('no running session with a side-output filter')
            run_settings.write(path, kind, prescale)
            with open(Path(self.state()['out']) / 'run-settings.jsonl', 'a') as f:
                f.write(json.dumps({'time': time.time(), 'kind': kind, 'prescale': prescale}) + '\n')
        return self.get_settings()

    def redis_port(self):
        s = self.state()
        if not s.get('running') or not s.get('out'):
            return None
        try:
            return redis_lite.chain_port(Path(s['out']) / 'timeframe')
        except (OSError, redis_lite.RedisError):
            return None  # the chain has not started its Redis yet

    def get_comment(self):
        port = self.redis_port()
        if port is None:
            return {'available': False, 'reason': 'no session with a running DAQ chain'}
        try:
            value = redis_lite.command(port, 'GET', COMMENT_KEY)
        except (OSError, redis_lite.RedisError) as exc:
            return {'available': False, 'reason': f'session Redis not reachable: {exc}'}
        return {'available': True, 'comment': value.decode(errors='replace') if value is not None else ''}

    def set_comment(self, text):
        text = clean_comment(text)
        with self.lock:
            port = self.redis_port()
            if port is None:
                raise ValueError('no session with a running DAQ chain')
            reply = redis_lite.command(port, 'SET', COMMENT_KEY, text.encode())
            if reply != 'OK':
                raise ValueError(f'unexpected reply from the session Redis: {reply!r}')
            with open(Path(self.state()['out']) / 'run-comments.jsonl', 'a') as f:
                f.write(json.dumps({'time': time.time(), 'comment': text}) + '\n')
        return self.get_comment()

    def _watch(self, proc, log, path):
        proc.wait()
        log.close()
        with self.lock:
            info = json.loads(path.read_text())
            if info.get('pid') == proc.pid:
                info.update(ended=time.time(), returncode=proc.returncode)
                path.write_text(json.dumps(info))


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}<title>E88 DAQ session</title>
<style>
body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:44rem;padding:0 1rem;color:#1a1a1a;background:#fff}}
h1{{font-size:1.4rem}} .box{{border:1px solid #ccc;border-radius:6px;padding:1rem;margin:1rem 0}}
.ok{{color:#176c2b}} .bad{{color:#a11}} .muted{{color:#666;font-size:.9rem}} code{{font-size:.85rem}}
input[type=number]{{width:8rem;font-size:1rem}} button{{font-size:1rem;padding:.3rem 1rem}}
</style></head><body>
<h1>E88 DAQ session</h1>
{body}
<p class="muted">A session reads every board in the setup file, enables the Receiver output, and hands RUN/STOP to the
controller page. The root HBF is switched on after RUN and off before STOP and when the session ends.
Press END PROCESS in the controller to finish a session.</p>
</body></html>"""


def render(sessions, message=''):
    s = sessions.state()
    esc = html.escape
    parts = []
    if message:
        parts.append(f'<div class="box bad">{esc(message)}</div>')
    refresh = ''
    if s.get('running'):
        if s['controller_up']:
            nofile = ' <b>No data file is written.</b>' if s.get('no_file') else ''
            parts.append(f'<div class="box ok">Session running (run {s["run_number"]}).{nofile} '
                         f'<a href="../daq-webctl.html">Open the controller</a></div>')
        else:
            refresh = '<meta http-equiv="refresh" content="2">'
            parts.append(f'<div class="box">Starting session for run {s["run_number"]}: checking the boards and '
                         f'starting the devices…</div>')
    else:
        last = ''
        if s.get('report'):
            r = s['report']
            cls = 'bad' if r.get('result') in ('FAIL_STOP', 'NO_DATA') else 'ok'
            last = (f'<div class="box"><b>Last session</b> (run {s.get("run_number")}): '
                    f'<span class="{cls}">{esc(str(r.get("result")))}</span>'
                    f'{"<br>" + esc(str(r.get("error"))) if r.get("error") else ""}'
                    f'{"<br>" + esc(str(r.get("note"))) if r.get("note") else ""}'
                    f'{"<br>RUN pressed: " + esc(str(r.get("runs_started"))) if r.get("runs_started") is not None else ""}'
                    f'<br>Root HBF after the session: {esc(str(r.get("root_hbf_post")))}'
                    f'<br><span class="muted">{esc(s.get("out", ""))}</span></div>')
        elif s.get('ended'):
            last = f'<div class="box bad">The last session (run {s.get("run_number")}) ended without a report; see {esc(s.get("log", ""))}.</div>'
        parts.append('<div class="box">No session is running.</div>' + last)
        p = sessions.prepare_state()
        if p.get('running'):
            refresh = '<meta http-equiv="refresh" content="2">'
            parts.append('<div class="box">Preparing the boards (initializing the HR-TDC boards that are not READY)…</div>')
            return PAGE.format(refresh=refresh, body='\n'.join(parts))
        if sessions.prepare_command():
            done = ''
            if p.get('report'):
                r = p['report']
                cls = 'ok' if r.get('result') == 'READY' else 'bad'
                when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(p.get('started', 0)))
                done = (f'<br>Last prepare ({esc(when)}): <span class="{cls}">{esc(str(r.get("result")))}</span>'
                        f'{"<br>" + esc(str(r.get("error"))) if r.get("error") else ""}'
                        f'{"<br>Not READY: " + esc(", ".join(r["not_ready"])) if r.get("not_ready") else ""}'
                        f'<br><span class="muted">{esc(p.get("out", ""))}</span>')
            elif p.get('ended'):
                done = f'<br><span class="bad">The last prepare ended without a report; see {esc(p.get("log", ""))}.</span>'
            parts.append(f'''<form class="box" method="post" action="prepare">
<button type="submit">Prepare boards</button>
<span class="muted">Initializes the HR-TDC boards that are not READY (needed after a power cycle or when a session
says "prepare required"). Boards that are READY are not touched.</span>{done}</form>''')
        parts.append(f'''<form class="box" method="post" action="start">
<label>Run number <input type="number" name="run_number" min="1" max="999999" required value="{sessions.next_run_number()}"></label>
<p><label><input type="checkbox" name="no_file" value="1"> Do not write data files (the online analyzer reads the data stream; no side-output filters)</label></p>
<button type="submit">Start session</button></form>''')
    return PAGE.format(refresh=refresh, body='\n'.join(parts))


class Handler(BaseHTTPRequestHandler):
    sessions = None

    def send(self, code, body, ctype='text/html; charset=utf-8', headers=()):
        data = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ('/', ''):
            self.send(200, render(self.sessions))
        elif path == '/status.json':
            self.send(200, json.dumps(self.sessions.state()), 'application/json')
        elif path == '/prepare.json':
            self.send(200, json.dumps(self.sessions.prepare_state()), 'application/json')
        elif path == '/recording.json':
            self.send(200, json.dumps(self.sessions.recording()), 'application/json')
        elif path == '/run-settings.json':
            self.send(200, json.dumps(self.sessions.get_settings()), 'application/json')
        elif path == '/run-comment.json':
            self.send(200, json.dumps(self.sessions.get_comment()), 'application/json')
        else:
            self.send(404, 'not found', 'text/plain')

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in ('/start', '/prepare', '/run-settings', '/run-comment'):
            return self.send(404, 'not found', 'text/plain')
        # Only accept requests from these pages (same host), not from other sites.
        origin = self.headers.get('Origin') or self.headers.get('Referer') or ''
        host = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or ''
        if urllib.parse.urlsplit(origin).netloc != host:
            if path in ('/run-settings', '/run-comment'):
                return self.send(403, json.dumps({'error': 'the request did not come from this page'}), 'application/json')
            return self.send(403, render(self.sessions, 'Refused: the request did not come from this page.'))
        if path == '/prepare':
            try:
                self.sessions.prepare()
            except (ValueError, OSError) as exc:
                return self.send(409, render(self.sessions, f'Not prepared: {exc}'))
            return self.send(303, '', headers=[('Location', './')])
        length = int(self.headers.get('Content-Length') or 0)
        form = urllib.parse.parse_qs(self.rfile.read(min(length, 4096)).decode(), keep_blank_values=True)
        if path == '/run-comment':
            try:
                result = self.sessions.set_comment(form.get('comment', [''])[0])
            except (ValueError, OSError, redis_lite.RedisError) as exc:
                return self.send(409, json.dumps({'error': str(exc)}), 'application/json')
            return self.send(200, json.dumps(result), 'application/json')
        if path == '/run-settings':
            try:
                kind = form.get('kind', [''])[0]
                prescale = int(form.get('prescale', [''])[0])
                result = self.sessions.set_settings(kind, prescale)
            except (ValueError, OSError) as exc:
                return self.send(409, json.dumps({'error': str(exc)}), 'application/json')
            return self.send(200, json.dumps(result), 'application/json')
        try:
            run_number = int(form.get('run_number', [''])[0])
            self.sessions.start(run_number, form.get('no_file', [''])[0] == '1')
        except (ValueError, OSError) as exc:
            return self.send(409, render(self.sessions, f'Not started: {exc}'))
        self.send(303, '', headers=[('Location', './')])

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--listen', default='127.0.0.1:18079')
    p.add_argument('--config', type=Path, default=ROOT / 'config/local/session.env')
    a = p.parse_args()
    Handler.sessions = Sessions(load_config(a.config), a.config)
    host, port = a.listen.rsplit(':', 1)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == '__main__':
    main()
