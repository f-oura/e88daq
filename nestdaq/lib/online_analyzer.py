"""Online analyzer that follows the run file of a running session.

The analyzer (nda-replay of the online analyzer) reads the file written by the FileSink with --follow, so it
never connects to the DAQ chain and cannot slow it down. When a new run file appears (the run number can change
at every RUN in the web controller), the analyzer of the previous run is stopped and a new one follows the new
file. The last analyzer keeps serving its histograms (--keep-alive) until the next run starts or the session ends.

A failing analyzer never stops the session: problems are only recorded.
"""
import socket
import subprocess
import time
from pathlib import Path

from run_storage import run_filename


def port_free(address):
    """True when ADDR:PORT can be bound now (the analyzer would get the port)."""
    host, port = address.rsplit(':', 1)
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
        except OSError:
            return False
    return True


def parse_http(address):
    """'127.0.0.1:18090' -> same string; rejects anything that is not ADDR:PORT."""
    host, sep, port = address.rpartition(':')
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f'analyzer address must be ADDR:PORT: {address!r}')
    return address


def analyzer_command(exe, data_file, http, param=None, expect_fem=(), extra=()):
    command = [str(exe), '--input', str(data_file), '--follow', '--keep-alive', '--quiet', '--http', f'http:{http}']
    if param:
        command += ['--param', str(param)]
    if expect_fem:
        command += ['--expect-fem', ','.join(expect_fem)]
    return command + list(extra)


class RunFileFollower:
    """Start one analyzer per run file, newest run only.

    data_dir     directory of run<NNNNNN>.dat
    started      session start (time.time()); older files are never followed
    command_for  (data_file, use_param) -> argv
    spawn        (argv, log_path) -> process with poll(), terminate(), wait(), kill(), returncode
    port_check   () -> bool, True when the analyzer port is free
    log_dir      where analyzer-run<NNNNNN>.log is written
    with_param   True when the analyzer is given the E88 parameters
    """

    def __init__(self, data_dir, started, command_for, spawn, port_check, log_dir, with_param=False,
                 param_fallback_seconds=20):
        self.data_dir = Path(data_dir)
        self.started = started
        self.command_for = command_for
        self.spawn = spawn
        self.port_check = port_check
        self.log_dir = Path(log_dir)
        self.with_param = with_param
        self.param_fallback_seconds = param_fallback_seconds
        self.current = None  # {'run', 'file', 'proc', 'start', 'param'}
        self.records = []

    def _stop_current(self, reason):
        cur, self.current = self.current, None
        if cur is None:
            return
        proc = cur['proc']
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        cur['record'].update(end=time.time(), returncode=proc.returncode, stopped=reason)

    def _start(self, run, data_file, use_param, note=None):
        record = {'run_number': run, 'data_file': str(data_file), 'start': time.time(), 'param': use_param}
        if note:
            record['note'] = note
        self.records.append(record)
        if not self.port_check():
            record['error'] = 'analyzer port in use; not started'
            self.current = None
            return
        command = self.command_for(data_file, use_param)
        record['command'] = command
        try:
            proc = self.spawn(command, self.log_dir / f'analyzer-run{run:06d}.log')
        except OSError as exc:
            record['error'] = f'analyzer did not start: {exc}'
            return
        self.current = {'run': run, 'file': data_file, 'proc': proc, 'start': time.monotonic(), 'param': use_param,
                        'record': record}

    def poll(self, run_number):
        """Call regularly with the current run number (None when unknown)."""
        cur = self.current
        if cur is not None and cur['proc'].poll() is not None:
            # An analyzer that fails soon after starting with parameters is retried once without them, so the
            # DAQ health and channel histograms are still shown.
            quick = time.monotonic() - cur['start'] < self.param_fallback_seconds
            if cur['proc'].returncode and cur['param'] and quick:
                self._stop_current('exited')
                self._start(cur['run'], cur['file'], False, note='restarted without parameters after an error')
                return
            self._stop_current('exited')
            # Remember the run so the same file is not followed again.
            self.current = {'run': cur['run'], 'file': cur['file'], 'proc': _Done(cur['proc'].returncode),
                            'start': cur['start'], 'param': cur['param'], 'record': {}}
            return
        if run_number is None:
            return
        try:
            data_file = self.data_dir / run_filename(int(run_number))
        except ValueError:
            return
        if cur is not None and cur['file'] == data_file:
            return
        try:
            st = data_file.stat()
        except OSError:
            return  # the FileSink has not opened the file yet
        # Older files are never followed. An empty file is not started on either: the FileSink creates the file at
        # RUN and writes the header with the first time frame, and the analyzer does not answer HTTP while it waits.
        if st.st_mtime < self.started or st.st_size == 0:
            return
        self._stop_current('next run')
        self._start(int(run_number), data_file, self.with_param)

    def close(self):
        self._stop_current('session end')
        return self.records


class _Done:
    """Placeholder for a finished analyzer (keeps its run from being started again)."""

    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass
