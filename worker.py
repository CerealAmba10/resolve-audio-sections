"""Background helper: receives a timeline from Resolve, opens the editor, then finishes teasers."""
import base64
import ctypes
import json
import os
import re
import shutil
import sys
import threading
import queue
import time
import tkinter as tk
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from tkinter import ttk

from editor import Editor
import updater
from media import frame_count, make_teaser, resource_path

PRESET = 'Audio Sections High'


def home_directory():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent / 'test-output/native-worker'


def read_pref(path, key):
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
    except ValueError:
        return None


def resolve_heartbeat(path):
    try:
        match = re.search(r'AudioSectionsHeartbeatV1\s*=\s*(\d+)', Path(path).read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return 0
    return int(match.group(1)) if match else 0


def lua_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, dict):
        return '{' + ','.join(f'[{lua_value(str(k))}]={lua_value(v)}' for k, v in value.items()) + '}'
    if isinstance(value, (list, tuple)):
        return '{' + ','.join(lua_value(v) for v in value) + '}'
    return '"' + ''.join('\\' + ch if ch in '"\\' else f'\\{ord(ch):03d}' if ord(ch) < 32 else ch
                         for ch in str(value)) + '"'


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


def write_state(home, request_id, status, **fields):
    atomic_text(Path(home) / 'state.lua', 'return ' + lua_value(dict(id=request_id, status=status, **fields)) + '\n')


def log(home, message):
    # Plain run log in the install folder, for checking what happened after a render.
    try:
        with open(Path(home) / 'log.txt', 'a', encoding='utf-8') as handle:
            handle.write(datetime.now().strftime('%Y-%m-%d %H:%M:%S  ') + message + '\n')
    except OSError:
        pass


def finish_teasers(batch, cancel, progress):
    """Cut each teaser from its rendered full clip and check the full clip's length."""
    fps, output, warnings = Fraction(batch['fps']), Path(batch['output']), []
    for n, job in enumerate(batch['jobs']):
        if cancel.is_set():
            break
        full = output / (job['name'] + '.mp4')
        if not full.is_file():
            raise ValueError(f'Resolve did not produce {full.name}.')
        frames = frame_count(full)
        if frames is not None and frames != job['frames']:
            warnings.append(f"{full.name}: {frames} frames rendered, {job['frames']} expected.")
        progress(n / len(batch['jobs']), f"Making teaser {job['teaser']}.mp4")
        make_teaser(full, output / (job['teaser'] + '.mp4'), batch['teaser_frames'], fps, batch['fade'])
    return warnings


class Worker:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        preset = resource_path(f'integration/{PRESET}.xml')
        if preset.is_file():
            shutil.copyfile(preset, self.home / f'{PRESET}.xml')
        self.settings_file = self.home / 'settings.json'
        try:
            self.settings = json.loads(self.settings_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.settings = {}
        self.prefs = Path(os.environ['APPDATA']) / 'Blackmagic Design/DaVinci Resolve/Support/Fusion/Profiles/Default/Fusion.prefs'
        self.seen = (read_pref(self.prefs, 'AudioSectionsRequestV2') or {}).get('id')
        self.result_seen = (read_pref(self.prefs, 'AudioSectionsResultV1') or {}).get('id')
        self.request = self.batch = None
        self.stage = 'idle'     # idle → editing → rendering → teasers → idle
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.root = tk.Tk()
        self.root.withdraw()
        ttk.Style().theme_use('vista' if 'vista' in ttk.Style().theme_names() else 'clam')
        self.window = tk.Toplevel(self.root)
        self.window.title('Audio Sections')
        self.window.geometry('480x210')
        self.window.resizable(False, False)
        self.window.withdraw()
        self.window.protocol('WM_DELETE_WINDOW', self.window.withdraw)
        frame = ttk.Frame(self.window, padding=24)
        frame.pack(fill='both', expand=True)
        self.title = tk.StringVar()
        self.message = tk.StringVar()
        ttk.Label(frame, textvariable=self.title, font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(frame, textvariable=self.message, wraplength=430).pack(anchor='w', pady=(10, 16))
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill='x')
        self.button = ttk.Button(frame, text='Stop', command=self.stop)
        self.button.pack(anchor='e', pady=(14, 0))
        self.root.after(250, self.poll)
        threading.Thread(target=self.check_updates, daemon=True).start()

    def check_updates(self):
        # Offline or rate-limited checks just try again later; scans never wait on the network.
        while True:
            try:
                found = updater.latest()
                if found:
                    self.events.put(('update', found))
            except Exception:
                pass
            time.sleep(6 * 3600)

    def offer_update(self, found):
        self.show('Update available', f"Audio Sections {found['version']} is ready (you have {updater.VERSION}). "
                  'Updating takes a few seconds; close this window to update later.', 0,
                  ('Update', lambda: self.install_update(found)))

    def install_update(self, found):
        self.stage = 'updating'
        self.show('Updating', f"Downloading Audio Sections {found['version']}…", 0, ('Close', self.window.withdraw))
        def work():
            try:
                updater.apply(found, self.home)
                self.events.put(('updated', None))
            except Exception as error:
                self.events.put(('error', f'Could not update: {error}'))
        threading.Thread(target=work, daemon=True).start()

    def show(self, title, message, progress=0, button=('Stop', None)):
        self.title.set(title)
        self.message.set(message)
        self.progress['value'] = progress
        self.button.configure(text=button[0], command=button[1] or self.stop)
        self.window.deiconify()
        self.window.lift()

    def done(self, title, message, folder=None):
        self.stage = 'idle'
        self.show(title, message, 100 if folder else 0,
                  ('Open export folder', lambda: os.startfile(folder)) if folder else ('Close', self.window.withdraw))

    def stop(self):
        self.cancel.set()
        if self.request:
            write_state(self.home, self.request['id'], 'cancelled', cancel=True)
        self.message.set('Stopping…')

    def open_editor(self, request):
        self.request, self.seen, self.stage = request, request['id'], 'editing'
        self.cancel.clear()
        self.window.withdraw()
        write_state(self.home, request['id'], 'editing')
        try:
            Editor(self.root, request, self.settings, self.render, self.cancelled)
        except Exception as error:
            write_state(self.home, request['id'], 'error', message=f'Could not read the timeline: {error}')
            self.done('Could not open', str(error))

    def cancelled(self):
        write_state(self.home, self.request['id'], 'cancelled')
        self.stage = 'idle'

    def render(self, batch):
        self.batch = batch
        self.stage = 'rendering'
        try:
            self.settings_file.write_text(json.dumps(self.settings, indent=2), encoding='utf-8')
        except OSError:
            pass
        batches = self.home / 'batches'
        batches.mkdir(exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        (batches / f'{stamp}.json').write_text(json.dumps(dict(request=self.request, batch=batch), indent=1), encoding='utf-8')
        jobs = [dict(name=j['name'], mark_in=j['mark_in'], mark_out=j['mark_out']) for j in batch['jobs']]
        write_state(self.home, self.request['id'], 'ready', preset=PRESET, output=Path(batch['output']).as_posix(), jobs=jobs)
        self.last_update = time.time()
        log(self.home, f"render requested: {', '.join(j['name'] for j in jobs)} -> {batch['output']}")
        self.show('Rendering your clips', f'Queuing {len(jobs)} clips in Resolve…')

    def teasers(self):
        log(self.home, 'Resolve finished rendering; making teasers')
        self.stage = 'teasers'
        self.show('Making teasers', 'Cutting teasers from the rendered clips…', 0)
        def work():
            try:
                warnings = finish_teasers(self.batch, self.cancel,
                                          lambda f, m: self.events.put(('progress', (f, m))))
                self.events.put(('finished', warnings))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if (self.home / 'shutdown.request').exists() and self.stage == 'idle':
            (self.home / 'worker.stopped').write_text('stopped', encoding='utf-8')
            self.root.destroy()
            return
        atomic_text(self.home / 'heartbeat.lua', f'return {{time={int(time.time())}}}\n')
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'progress':
                self.progress['value'] = value[0] * 100
                self.message.set(value[1])
            elif kind == 'update':
                if self.stage == 'idle':
                    self.offer_update(value)
            elif kind == 'updated':
                # The new setup now asks this idle helper to exit, replaces it and starts the new one.
                self.stage = 'idle'
                self.message.set('Installing the update. Audio Sections restarts by itself.')
            elif kind == 'finished':
                count = len(self.batch['jobs'])
                log(self.home, f"done: {count} clips, teasers {', '.join(j['teaser'] for j in self.batch['jobs'])}"
                    + (f" (warnings: {' '.join(value)})" if value else ''))
                note = '\n\nCheck: ' + ' '.join(value) if value else ''
                self.done('All done' if not self.cancel.is_set() else 'Stopped',
                          f'{count} clips and {count} teasers saved.{note}', self.batch['output'])
            else:
                log(self.home, f'error: {value}')
                self.done('Could not finish', value)
        result = read_pref(self.prefs, 'AudioSectionsResultV1')
        if result and result.get('id') != self.result_seen and result.get('status') in ('error', 'complete'):
            self.result_seen = result.get('id')
            if result['status'] == 'error' and self.stage != 'teasers':
                log(self.home, f"Resolve error: {result.get('message', '')}")
                self.done('Could not finish', result.get('message', ''))
            elif result['status'] == 'complete' and self.stage == 'rendering' and result['id'] == self.seen:
                self.teasers()
        elif self.stage == 'rendering' and result and result.get('id') == self.seen and result.get('status') == 'rendering':
            self.message.set(result.get('message', ''))
            self.progress['value'] = result.get('progress', 0)
        if self.stage == 'rendering' and time.time() - max(self.last_update, resolve_heartbeat(self.prefs)) > 90:
            log(self.home, 'gave up: no word from Resolve for 90 s')
            self.done('Could not finish', 'Resolve stopped responding. Check its render queue, then run Audio Sections again.')
        request = read_pref(self.prefs, 'AudioSectionsRequestV2')
        if self.stage == 'idle' and request and request.get('id') != self.seen:
            self.open_editor(request)
        self.root.after(500, self.poll)


def main():
    # One helper per signed-in Windows session; never kill or replace other programs.
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, 'Local\\AudioSectionsWorkerV3')
    if kernel.GetLastError() == 183:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    try:
        Worker(home_directory()).root.mainloop()
    finally:
        kernel.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == '__main__':
    main()
