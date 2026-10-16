"""Quiet background helper; a simple progress window appears only for a menu request."""
import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from audio_sections import Cancelled
from worker_core import atomic_text, read_pref, sort_recording, write_state


def home_directory():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent / 'test-output/native-worker'


class Worker:
    def __init__(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.prefs = Path(os.environ['APPDATA']) / 'Blackmagic Design/DaVinci Resolve/Support/Fusion/Profiles/Default/Fusion.prefs'
        previous = read_pref(self.prefs, 'AudioSectionsRequestV1') or {}
        self.seen = previous.get('id')
        self.result_seen = (read_pref(self.prefs, 'AudioSectionsResultV1') or {}).get('id')
        self.request = None
        self.busy = False
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.root = tk.Tk()
        self.root.title('Audio Sections')
        self.root.geometry('470x210')
        self.root.resizable(False, False)
        self.root.withdraw()
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill='both', expand=True)
        self.title = tk.StringVar(value='Sorting your recording')
        self.message = tk.StringVar(value='Looking for audio sections…')
        ttk.Label(frame, textvariable=self.title, font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(frame, textvariable=self.message, wraplength=420).pack(anchor='w', pady=(10, 16))
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill='x')
        self.button = ttk.Button(frame, text='Stop', command=self.stop)
        self.button.pack(anchor='e', pady=(14, 0))
        self.root.protocol('WM_DELETE_WINDOW', self.root.withdraw)
        self.root.after(250, self.poll)

    def stop(self):
        self.cancel.set()
        if self.request:
            write_state(self.home, self.request['id'], 'cancelled', 'Stopped.', cancel=True)
        self.message.set('Stopping…')

    def start(self, request):
        self.request = request
        self.seen = request['id']
        self.busy = True
        self.cancel.clear()
        self.progress['value'] = 0
        self.title.set('Sorting your recording')
        self.message.set('Looking for audio sections…')
        self.button.configure(text='Stop', command=self.stop)
        self.root.deiconify()
        self.root.lift()
        write_state(self.home, self.seen, 'scanning')
        def work():
            try:
                count, output = sort_recording(request, self.home, self.cancel,
                    lambda value: self.events.put(('progress', value)))
                self.events.put(('ready', (count, output)))
            except Cancelled:
                self.events.put(('cancelled', 'Stopped.'))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        if (self.home / 'shutdown.request').exists() and not self.busy:
            (self.home / 'worker.stopped').write_text('stopped', encoding='utf-8')
            self.root.destroy()
            return
        atomic_text(self.home / 'heartbeat.lua', f'return {{time={int(time.time())}}}\n')
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if self.cancel.is_set() and kind in ('progress', 'ready'):
                continue
            if kind == 'progress':
                self.progress['value'] = value * 100
                write_state(self.home, self.seen, 'scanning', progress=value * 100)
            elif kind == 'ready':
                self.count, self.output = value
                self.message.set(f'{self.count} clips found. Adding them to Resolve…')
                write_state(self.home, self.seen, 'ready')
            else:
                self.busy = False
                self.title.set('Stopped' if kind == 'cancelled' else 'Could not finish')
                self.message.set(value)
                self.button.configure(text='Close', command=self.root.withdraw)
                write_state(self.home, self.seen, kind, value)
        result = read_pref(self.prefs, 'AudioSectionsResultV1')
        if result and result.get('status') == 'error' and result.get('id') != self.result_seen:
            self.result_seen = result.get('id')
            self.root.deiconify()
            self.title.set('Could not finish')
            self.message.set(result.get('message', ''))
            self.button.configure(text='Close', command=self.root.withdraw)
        if self.busy and result and result.get('id') == self.seen:
            self.message.set(result.get('message', ''))
            self.progress['value'] = result.get('progress', 0)
            if result['status'] == 'rendering':
                self.title.set('Rendering your clips')
            elif result['status'] in ('complete', 'error'):
                self.busy = False
                self.title.set('All done' if result['status'] == 'complete' else 'Could not finish')
                self.button.configure(text='Open export folder' if result['status'] == 'complete' else 'Close',
                    command=(lambda: os.startfile(self.output)) if result['status'] == 'complete' else self.root.withdraw)
        request = read_pref(self.prefs, 'AudioSectionsRequestV1')
        if not self.busy and request and request.get('id') != self.seen:
            self.start(request)
        self.root.after(500, self.poll)


def main():
    # One helper per signed-in Windows session; never kill or replace other programs.
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, 'Local\\AudioSectionsWorkerV3')
    if kernel.GetLastError() == 183:
        return
    try:
        Worker(home_directory()).root.mainloop()
    finally:
        kernel.CloseHandle(ctypes.c_void_p(mutex))


if __name__ == '__main__':
    main()
