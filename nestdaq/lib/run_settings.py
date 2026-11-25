"""Settings file of the side-output filter, read by the filter device at every RUN.

    kind sc|prescale|off
    prescale N            (N >= 1)

The launcher writes the settings given at the start of the session; the session page replaces them between runs.
The file is replaced atomically, so the device never reads half of it.
"""
import os
from pathlib import Path

KINDS = ('sc', 'prescale', 'off')


def check(kind, prescale):
    if kind not in KINDS:
        raise ValueError('kind must be sc, prescale or off')
    if not isinstance(prescale, int) or isinstance(prescale, bool) or not 1 <= prescale <= 10**9:
        raise ValueError('prescale must be an integer from 1 to 1000000000')


def write(path, kind, prescale):
    check(kind, prescale)
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(f'kind {kind}\nprescale {prescale}\n')
    os.replace(tmp, path)


def read(path):
    """{'kind': ..., 'prescale': ...} from the file; ValueError when it is not in the format above."""
    values = {}
    for line in Path(path).read_text().splitlines():
        words = line.split('#', 1)[0].split()
        if not words:
            continue
        if len(words) != 2 or words[0] not in ('kind', 'prescale'):
            raise ValueError(f'bad line in {path}: {line!r}')
        values[words[0]] = words[1]
    kind = values.get('kind')
    text = values.get('prescale', '')
    if not text.isdigit() or (len(text) > 1 and text[0] == '0'):
        raise ValueError(f'bad prescale in {path}')
    check(kind, int(text))
    return {'kind': kind, 'prescale': int(text)}
