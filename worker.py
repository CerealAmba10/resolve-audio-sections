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
import traceback
import tkinter as tk
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from tkinter import messagebox, ttk

import detection as d
from editor import Editor
import updater
from media import finish_full, frame_count, make_teaser, resource_path, video_size
from thumbnail import deal_titles, load_titles, render as render_thumbnail

PRESET = 'Audio Sections High'
SETTINGS_SCHEMA = 2      # bump when cutting defaults change, so saved old values are reset


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


def result_key(result):
    """Identity of one message from Resolve: commands in one session share an id, so status and seq count too."""
    return result and (result.get('id'), result.get('status'), result.get('seq'), result.get('progress'))


def project_key(request):
    """Resolve's project id survives renames; older Resolve scripts only send the name."""
    return request.get('project_id') or request.get('project') or ''


def pieces_of(timeline):
    """Clips of a reviewed cuts timeline: enabled items on its first non-empty track (video first), with markers."""
    track = next((t for k in ('video', 'audio') for t in timeline[k] if t['items']), None)
    items = [i for i in (track['items'] if track else []) if i.get('enabled', True)]
    return d.pieces_from(items, timeline.get('markers') or [], int(timeline['start']), d.parse_fps(timeline['fps']))


def finish_clip(batch, job):
    """Cut one teaser from its rendered full clip, then finish the clip (fade-out, thumbnail). Returns a warning or None.
    With a thumbnail, both files open on it for the clip's chosen seconds, get it as their cover, and it is saved
    beside them as a .png; the teaser shows "TEASER: N", the full clip only if asked."""
    fps, output = Fraction(batch['fps']), Path(batch['output'])
    full = output / (job['name'] + '.mp4')
    if not full.is_file():
        raise ValueError(f'Resolve did not produce {full.name}.')
    frames = frame_count(full)
    teaser_thumb = full_thumb = None
    thumb, seconds = batch.get('thumb'), job.get('thumb_seconds', 0)
    if thumb:
        width, height = video_size(full)
        label = f"TEASER: {job['teaser_index']}"
        teaser_thumb, full_thumb = output / (job['teaser'] + '.png'), output / (job['name'] + '.png')
        fonts = dict(font_path=thumb.get('title_font') or None, label_font=thumb.get('label_font') or None)
        render_thumbnail(thumb['background'], job.get('title', ''), label, width, height, **fonts).save(teaser_thumb)
        render_thumbnail(thumb['background'], job.get('title', ''), label if thumb.get('full_label') else '',
                         width, height, **fonts).save(full_thumb)
    make_teaser(full, output / (job['teaser'] + '.mp4'), job.get('teaser_frames', batch['teaser_frames']), fps,
                batch['fade'], teaser_thumb, seconds)
    # After the teaser, so the teaser is cut from the untouched clip.
    finish_full(full, frames or job['frames'], fps, batch.get('clip_fade', 0), full_thumb, seconds)
    if frames is not None and frames != job['frames']:
        return f"{full.name}: {frames} frames rendered, {job['frames']} expected."


def timeline_key(request):
    """Teasers are counted per project and timeline; a cuts timeline counts toward the timeline it came from."""
    name = request['timeline']['name']
    while re.search(r' \(cuts( \d+)?\)$', name):
        name = re.sub(r' \(cuts( \d+)?\)$', '', name)
    return f'{project_key(request)}|{name}'


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
        if self.settings.get('schema') != SETTINGS_SCHEMA:
            # Cutting defaults changed: drop saved cutting values once, keep track roles.
            self.settings = dict(roles=self.settings.get('roles', {}), schema=SETTINGS_SCHEMA)
        self.prefs = Path(os.environ['APPDATA']) / 'Blackmagic Design/DaVinci Resolve/Support/Fusion/Profiles/Default/Fusion.prefs'
        self.seen = (read_pref(self.prefs, 'AudioSectionsRequestV2') or {}).get('id')
        self.result_seen = result_key(read_pref(self.prefs, 'AudioSectionsResultV1'))
        self.request = self.batch = self.editor = self.record = None
        self.seq, self.waiting = 0, None
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
        self.window.protocol('WM_DELETE_WINDOW', self.close_window)
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
        threading.Thread(target=self.beat, daemon=True).start()

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

    def check_now(self):
        """The window's Check for updates button: ask GitHub straight away, off the window's thread."""
        if self.editor:
            self.editor.status.set('Checking for updates…')
        def work():
            try:
                self.events.put(('checked', updater.latest()))
            except Exception as error:
                self.events.put(('checked', f'Could not reach GitHub: {error}'))
        threading.Thread(target=work, daemon=True).start()

    def answer_check(self, found):
        editor = self.editor
        parent = editor.win if editor else self.window
        if editor:
            editor.status.set('')
        if isinstance(found, str):
            return messagebox.showerror('Check for updates', found, parent=parent)
        if not found:
            return messagebox.showinfo('Check for updates', f'You have the latest version ({updater.VERSION}).', parent=parent)
        if messagebox.askyesno('Update available', f"Audio Sections {found['version']} is available (you have "
                               f"{updater.VERSION}).\n\nUpdate now? This closes this window and restarts the helper; "
                               'anything already in Resolve, such as a cuts timeline, stays.', parent=parent):
            if editor and self.stage == 'editing':
                editor.close_quietly()
                self.cancelled()          # lets the waiting Resolve script finish cleanly
            self.install_update(found)

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

    def close_window(self):
        self.window.withdraw()

    def cuts_registry(self):
        try:
            return json.loads((self.home / 'cuts.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def open_editor(self, request):
        self.request, self.seen, self.stage, self.seq, self.waiting = request, request['id'], 'editing', 0, None
        self.cancel.clear()
        self.window.withdraw()
        write_state(self.home, request['id'], 'editing')
        try:
            # A cuts timeline made earlier opens with its pieces as the clips, and its saved name and folder.
            record = self.cuts_registry().get(request['timeline']['name'])
            # The project has the authority over clip name and folder: what was last used in this project wins
            # over a cuts timeline's record; a project's first run is named after the project.
            remembered = self.settings.get('projects', {}).get(project_key(request), {})
            names = {**(record or {}), **remembered}
            names.setdefault('stem', request.get('project') or '')
            self.editor = Editor(self.root, request, self.settings, self.render, self.cancelled, self.send,
                                 pieces=record and pieces_of(request['timeline']), defaults=names, home=self.home,
                                 check_updates=self.check_now)
        except Exception as error:
            write_state(self.home, request['id'], 'error', message=f'Could not read the timeline: {error}')
            self.done('Could not open', str(error))

    def send(self, command, record=None):
        """A command from the open window to the waiting Resolve script; its reply goes back to the window."""
        if record:
            self.remember_project(record['stem'], record['output'])
        if command['action'] == 'cuts':      # keep what was asked for, to check against Resolve later
            batches = self.home / 'batches'
            batches.mkdir(exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            (batches / f'{stamp} cuts.json').write_text(json.dumps(dict(request=self.request, command=command), indent=1),
                                                        encoding='utf-8')
        self.seq += 1
        self.waiting, self.record = time.time(), record
        write_state(self.home, self.request['id'], 'ready', seq=self.seq, **command)
        log(self.home, f"sent {command['action']} to Resolve")

    def reply(self, result):
        self.waiting = None
        if result['status'] == 'undone':
            registry = self.cuts_registry()
            registry.pop(result['timeline'], None)
            atomic_text(self.home / 'cuts.json', json.dumps(registry, indent=1))
            if result.get('ended'):
                write_state(self.home, self.request['id'], 'cancelled')
                self.stage, self.editor = 'idle', None
                self.done('Cuts timeline deleted', result.get('message', ''))
        if result['status'] == 'cuts_ready' and self.record:
            registry = self.cuts_registry()
            registry[result['timeline']] = self.record
            atomic_text(self.home / 'cuts.json', json.dumps(registry, indent=1))
        log(self.home, f"Resolve: {result['status']} {result.get('message', '')}")
        if self.editor:
            self.editor.on_result(result)

    def cancelled(self):
        write_state(self.home, self.request['id'], 'cancelled')
        self.stage, self.editor = 'idle', None

    def remember_project(self, stem, output, thumb=None):
        remembered = self.settings.setdefault('projects', {}).setdefault(project_key(self.request), {})
        remembered.update(stem=stem, output=output)
        if thumb:
            remembered.update(thumb_background=thumb['background'], thumb_titles=thumb['titles'],
                              thumb_seconds=thumb['seconds'], thumb_full_label=thumb['full_label'],
                              title_font=thumb.get('title_font', ''), label_font=thumb.get('label_font', ''))
        try:
            self.settings_file.write_text(json.dumps(self.settings, indent=2), encoding='utf-8')
        except OSError:
            pass

    def render(self, batch):
        self.batch, self.editor, self.stage = batch, None, 'rendering'
        if batch.get('thumb'):
            # Next "TEASER: N" numbers for this timeline, and one title per clip pair from the titles file.
            counts = self.settings.setdefault('teaser_counts', {})
            key = timeline_key(self.request)
            titles = deal_titles(load_titles(batch['thumb']['titles']), len(batch['jobs'])) if batch['thumb']['titles'] else []
            for n, job in enumerate(batch['jobs']):
                job['teaser_index'] = counts.get(key, 0) + n + 1
                job['title'] = titles[n] if n < len(titles) else ''
            counts[key] = counts.get(key, 0) + len(batch['jobs'])
        self.remember_project(batch['stem'], batch['output'], batch.get('thumb'))     # also saves the other settings
        batches = self.home / 'batches'
        batches.mkdir(exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        (batches / f'{stamp}.json').write_text(json.dumps(dict(request=self.request, batch=batch), indent=1), encoding='utf-8')
        jobs = [dict(name=j['name'], mark_in=j['mark_in'], mark_out=j['mark_out']) for j in batch['jobs']]
        self.seq += 1
        target = {'timeline': batch['timeline']} if batch.get('timeline') else {}
        write_state(self.home, self.request['id'], 'ready', seq=self.seq, action='render', preset=PRESET,
                    output=Path(batch['output']).as_posix(), jobs=jobs, **target)
        self.last_update = time.time()
        self.teaser_queue, self.teasers_sent, self.teasers_made = queue.Queue(), set(), 0
        threading.Thread(target=self.make_teasers, args=(batch, self.teaser_queue), daemon=True).start()
        log(self.home, f"render requested: {', '.join(j['name'] for j in jobs)} from "
            f"{batch.get('timeline') or self.request['timeline']['name']} -> {batch['output']}")
        self.show('Rendering your clips', f'Queuing {len(jobs)} clips in Resolve…')

    def make_teasers(self, batch, jobs):
        """Teaser worker: cuts each clip's teaser as soon as Resolve reports that clip finished.
        Gets job names while Resolve renders, then None once rendering is complete."""
        warnings = []
        try:
            while True:
                name = jobs.get()
                if name is None:
                    return self.events.put(('finished', warnings))
                job = next(j for j in batch['jobs'] if j['name'] == name)
                self.events.put(('teaser', name))
                warning = finish_clip(batch, job)
                warnings += [warning] if warning else []
                self.events.put(('teaser_done', job['teaser']))
        except Exception as error:
            self.events.put(('error', str(error)))

    def queue_teasers(self, names):
        for name in names:
            if name not in self.teasers_sent:
                self.teasers_sent.add(name)
                self.teaser_queue.put(name)

    def beat(self):
        # Resolve's script treats a heartbeat older than 15 s as a dead helper. Writing it from its own thread
        # keeps it fresh while the window is busy or a dialog is open.
        while True:
            try:
                atomic_text(self.home / 'heartbeat.lua', f'return {{time={int(time.time())}}}\n')
            except OSError:
                pass
            time.sleep(2)

    def poll(self):
        if (self.home / 'shutdown.request').exists() and self.stage == 'idle':
            (self.home / 'worker.stopped').write_text('stopped', encoding='utf-8')
            self.root.destroy()
            return
        try:
            self.step()
        except Exception:
            # One bad message must never stop the helper: it would miss "render complete" and the teasers.
            log(self.home, 'helper error:\n' + traceback.format_exc())
        self.root.after(500, self.poll)

    def step(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'progress':
                self.progress['value'] = value[0] * 100
                self.message.set(value[1])
            elif kind == 'checked':
                self.root.after(0, self.answer_check, value)     # dialogs never hold up the loop
            elif kind == 'update':
                if self.stage == 'idle':
                    self.offer_update(value)
            elif kind == 'updated':
                # The new setup now asks this idle helper to exit, replaces it and starts the new one.
                self.stage = 'idle'
                self.message.set('Installing the update. Audio Sections restarts by itself.')
            elif kind == 'teaser':
                log(self.home, f'cutting teaser of {value}')
            elif kind == 'teaser_done':
                self.teasers_made += 1
                if self.stage == 'teasers':
                    count = len(self.batch['jobs'])
                    self.progress['value'] = self.teasers_made / count * 100
                    self.message.set(f'{self.teasers_made} of {count} teasers made…')
            elif kind == 'finished':
                if self.stage != 'teasers':
                    continue          # the worker was stopped because Resolve failed; that is already shown
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
        if result and result_key(result) != self.result_seen:
            self.result_seen = result_key(result)
            mine = result.get('id') == self.seen
            if result['status'] in ('cuts_ready', 'pieces', 'undone', 'command_error') and mine and self.stage == 'editing':
                self.reply(result)
            elif result['status'] == 'error' and self.stage != 'teasers':
                log(self.home, f"Resolve error: {result.get('message', '')}")
                if self.stage == 'rendering':
                    self.teaser_queue.put(None)     # stop the teaser worker; clips that finished keep their teasers
                if self.editor:
                    self.editor.close_quietly()
                    self.editor = None
                self.done('Could not finish', result.get('message', ''))
            elif result['status'] == 'complete' and self.stage == 'rendering' and mine:
                log(self.home, 'Resolve finished rendering; finishing teasers')
                self.queue_teasers(j['name'] for j in self.batch['jobs'])
                self.teaser_queue.put(None)
                self.stage = 'teasers'
                count = len(self.batch['jobs'])
                self.show('Making teasers', f'{self.teasers_made} of {count} teasers made…', self.teasers_made / count * 100)
            elif result['status'] == 'rendering' and self.stage == 'rendering' and mine:
                self.queue_teasers(result.get('finished') or [])
                self.message.set(result.get('message', '') + (f' · {self.teasers_made} teasers made' if self.teasers_made else ''))
                self.progress['value'] = result.get('progress', 0)
        if self.waiting and time.time() - self.waiting > 120 and self.editor:
            self.waiting = None
            self.editor.on_result(dict(status='command_error', message='Resolve did not answer. Is it still open with '
                                       'the Audio Sections script running? Close this window and run it again.'))
        # Queueing many jobs and saving a big project can take minutes; Resolve reports each queued job.
        if self.stage == 'rendering' and time.time() - max(self.last_update, resolve_heartbeat(self.prefs)) > 300:
            log(self.home, 'gave up: no word from Resolve for 5 minutes')
            self.done('Could not finish', 'Resolve stopped responding. Check its render queue, then run Audio Sections again.')
        request = read_pref(self.prefs, 'AudioSectionsRequestV2')
        if self.stage == 'idle' and request and request.get('id') != self.seen:
            self.open_editor(request)


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
