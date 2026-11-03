"""Shared six-digit E88 run naming; explicit allocation, no automatic reuse."""
from pathlib import Path


def run_filename(number):
    if not isinstance(number,int) or isinstance(number,bool) or not 0<=number<=999999:
        raise ValueError('run number must be an integer from 0 through 999999')
    return f'run{number:06d}.dat'


def output_path(directory,number):
    directory=Path(directory).resolve()
    path=directory/run_filename(number)
    if not directory.is_dir():raise ValueError('data directory must already exist')
    if path.exists() or path.is_symlink():raise ValueError(f'refusing existing run file: {path}')
    return path
