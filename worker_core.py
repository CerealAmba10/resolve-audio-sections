"""Local-only mailbox and scan operations for the one-click Resolve integration."""
import base64
import json
import os
import re
import shutil
import time
from pathlib import Path

from audio_sections import lua_string, scan_media, prepare_queue


def read_pref(path, key):
    if not Path(path).is_file():
        return None
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    match = re.search(r'\b' + re.escape(key) + r'\s*=\s*"([A-Za-z0-9+/=]+)"', text)
    if not match:
        return None
    try:
        value = json.loads(base64.b64decode(match.group(1), validate=True))
        return value if isinstance(value, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def atomic_text(path, text):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(text, encoding='utf-8')
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.01)


def write_state(home, request_id, status, message='', progress=0, cancel=False):
    text = 'return {id=' + lua_string(request_id) + ',status=' + lua_string(status)
    text += ',message=' + lua_string(message) + f',progress={float(progress):.3f},cancel=' + ('true' if cancel else 'false') + '}\n'
    atomic_text(Path(home) / 'state.lua', text)


def next_number(source, output, requested):
    pattern = re.compile(re.escape(Path(source).stem) + r' (\d+)T?\.mp4$', re.IGNORECASE)
    maximum = int(requested) - 1
    if Path(output).exists():
        for path in Path(output).iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                maximum = max(maximum, int(match.group(1)))
    return maximum + 1


def sort_recording(request, home, cancel, progress):
    request_id = request['id']
    if not re.fullmatch(r'\d+-\d+', request_id):
        raise ValueError('Invalid recording request.')
    source = Path(request['source'])
    if not source.is_file():
        raise ValueError('The recording is offline. Relink it in Resolve and try again.')
    output = source.parent / 'Audio Sections Exports'
    info, sections = scan_media(source, -40, 1, 'all', cancel, progress)
    number = next_number(source, output, request.get('first_number', 1))
    folder = prepare_queue(source, output, info, sections, number, 1, 'Audio Sections High')
    target = Path(home) / 'batches' / request_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError('This recording request was already handled.')
    shutil.copytree(folder, target)
    return len(sections) * 2, output
