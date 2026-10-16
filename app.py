"""Desktop companion; all analysis stays on the local machine."""
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from audio_sections import Cancelled, lua_string, prepare_queue, scan_media, resource_path


def timestamp(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{int(hours):02}:{int(minutes):02}:{seconds:06.3f}'


class App:
    def __init__(self, root):
        self.root = root
        root.title('Audio Sections · DaVinci Resolve')
        root.geometry('920x730')
        root.minsize(780, 660)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.result = None
        self.batch = None
        self.vars = {key: tk.StringVar(value=value) for key, value in dict(
            source='', output='', threshold='-40', gap='1', fade='1', number='1',
            preset='Audio Sections High', audio='all').items()}
        self.status = tk.StringVar(value='Choose a video file. Analysis works entirely offline.')
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background='#f3f3ef')
        style.configure('TLabel', background='#f3f3ef', font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 22, 'bold'))
        style.configure('TButton', font=('Segoe UI', 10), padding=(12, 7))
        style.configure('Treeview', rowheight=29, font=('Segoe UI', 10))
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(7, weight=1)
        ttk.Label(outer, text='Audio Sections', style='Title.TLabel').grid(row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(outer, text='Find the audio. Queue the teaser, then the full clip.').grid(
            row=1, column=0, columnspan=3, sticky='w', pady=(3, 20))
        for row, key, label, command in [(2, 'source', 'Video file', self.browse_source),
                                         (3, 'output', 'Export folder', self.browse_output)]:
            ttk.Label(outer, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12))
            ttk.Entry(outer, textvariable=self.vars[key]).grid(row=row, column=1, sticky='ew', pady=5)
            ttk.Button(outer, text='Browse…', command=command).grid(row=row, column=2, padx=(10, 0))
        options = ttk.Frame(outer)
        options.grid(row=4, column=0, columnspan=3, sticky='ew', pady=16)
        fields = [('Silence (dBFS)', 'threshold'), ('Minimum gap (s)', 'gap'),
                  ('Teaser fade (s)', 'fade'), ('Start number', 'number'), ('Audio stream', 'audio')]
        for column, (label, key) in enumerate(fields):
            options.columnconfigure(column, weight=1)
            ttk.Label(options, text=label).grid(row=0, column=column, sticky='w', padx=(0, 10))
            ttk.Entry(options, textvariable=self.vars[key], width=14).grid(
                row=1, column=column, sticky='ew', padx=(0, 10), pady=6)
        ttk.Label(options, text='Fade: 0 to disable. Audio: all, or stream number starting at 0. Teasers: 30 seconds.').grid(
            row=2, column=0, columnspan=5, sticky='w')
        preset = ttk.Frame(outer)
        preset.grid(row=5, column=0, columnspan=3, sticky='ew', pady=(0, 14))
        preset.columnconfigure(1, weight=1)
        ttk.Label(preset, text='Resolve Deliver preset').grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(preset, textvariable=self.vars['preset']).grid(row=0, column=1, sticky='ew')
        ttk.Button(preset, text='Setup guide', command=self.open_guide).grid(row=0, column=2, padx=(10, 0))
        controls = ttk.Frame(outer)
        controls.grid(row=6, column=0, columnspan=3, sticky='ew', pady=(0, 12))
        self.scan_button = ttk.Button(controls, text='1. Scan file', command=self.scan)
        self.scan_button.pack(side='left')
        self.cancel_button = ttk.Button(controls, text='Cancel', command=self.cancel.set, state='disabled')
        self.cancel_button.pack(side='left', padx=8)
        self.prepare_button = ttk.Button(controls, text='2. Prepare Resolve queue', command=self.prepare, state='disabled')
        self.prepare_button.pack(side='right')
        table_frame = ttk.Frame(outer)
        table_frame.grid(row=7, column=0, columnspan=3, sticky='nsew')
        self.table = ttk.Treeview(table_frame, columns=('number', 'start', 'end', 'duration'), show='headings', height=8)
        for column, label, width in [('number', 'Section', 80), ('start', 'Start', 190),
                                      ('end', 'End', 190), ('duration', 'Full clip length', 180)]:
            self.table.heading(column, text=label)
            self.table.column(column, width=width, anchor='w')
        scrollbar = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.table.pack(side='left', fill='both', expand=True)
        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(14, 8))
        ttk.Label(outer, textvariable=self.status, wraplength=810).grid(row=9, column=0, columnspan=3, sticky='w')
        bottom = ttk.Frame(outer)
        bottom.grid(row=10, column=0, columnspan=3, sticky='ew', pady=(12, 0))
        self.copy_button = ttk.Button(bottom, text='3. Copy Resolve command', command=self.copy_command, state='disabled')
        self.copy_button.pack(side='left')
        self.folder_button = ttk.Button(bottom, text='Open batch folder', command=self.open_folder, state='disabled')
        self.folder_button.pack(side='left', padx=8)
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.poll)

    def browse_source(self):
        filename = filedialog.askopenfilename(title='Choose a media file', filetypes=[
            ('Video files', '*.mp4 *.mov *.mkv *.avi *.mxf *.mts *.m2ts *.webm'), ('All files', '*.*')])
        if filename:
            self.vars['source'].set(filename)
            if not self.vars['output'].get():
                self.vars['output'].set(str(Path(filename).parent / 'Audio Sections Exports'))

    def browse_output(self):
        folder = filedialog.askdirectory(title='Choose the export folder')
        if folder:
            self.vars['output'].set(folder)

    def analysis_inputs(self):
        source = Path(self.vars['source'].get()).resolve()
        if not source.is_file():
            raise ValueError('Choose an existing video file.')
        return source, float(self.vars['threshold'].get()), float(self.vars['gap'].get()), self.vars['audio'].get().strip()

    def scan(self):
        try:
            inputs = self.analysis_inputs()
            stat = inputs[0].stat()
        except (ValueError, OSError) as error:
            messagebox.showerror('Cannot scan', str(error))
            return
        self.busy = True
        self.result = None
        self.batch = None
        self.cancel.clear()
        self.table.delete(*self.table.get_children())
        self.progress['value'] = 0
        for button in (self.scan_button, self.prepare_button, self.copy_button, self.folder_button):
            button['state'] = 'disabled'
        self.cancel_button['state'] = 'normal'
        self.status.set('Scanning audio only. Long recordings can take several minutes…')
        def work():
            try:
                info, sections = scan_media(*inputs, self.cancel, lambda p: self.events.put(('progress', p)))
                after = inputs[0].stat()
                fingerprint = (stat.st_size, stat.st_mtime_ns)
                if fingerprint != (after.st_size, after.st_mtime_ns):
                    raise ValueError('The source changed during scanning. Scan it again.')
                self.events.put(('done', (inputs, fingerprint, info, sections)))
            except Cancelled:
                self.events.put(('cancelled', None))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'progress':
                self.progress['value'] = value * 100
                continue
            self.busy = False
            self.scan_button['state'] = 'normal'
            self.cancel_button['state'] = 'disabled'
            if kind == 'done':
                self.result = value
                sections = value[3]
                for n, (start, end) in enumerate(sections, 1):
                    self.table.insert('', 'end', values=(n, timestamp(start), timestamp(end), timestamp(end - start)))
                self.progress['value'] = 100
                self.status.set(f'{len(sections)} sections found → {len(sections) * 2} render jobs. Review the boundaries above.')
                self.prepare_button['state'] = 'normal' if sections else 'disabled'
            elif kind == 'cancelled':
                self.status.set('Scan cancelled.')
            else:
                self.status.set('Scan failed. Adjust the settings or choose another file.')
                messagebox.showerror('Scan failed', value)
        self.root.after(100, self.poll)

    def prepare(self):
        try:
            inputs, fingerprint, info, sections = self.result
            current = self.analysis_inputs()
            stat = current[0].stat()
            if current != inputs or fingerprint != (stat.st_size, stat.st_mtime_ns):
                raise ValueError('The source or scan settings changed. Scan again before preparing.')
            if not self.vars['output'].get().strip():
                raise ValueError('Choose an export folder.')
            self.batch = prepare_queue(inputs[0], self.vars['output'].get(), info, sections,
                                       int(self.vars['number'].get()), float(self.vars['fade'].get()),
                                       self.vars['preset'].get())
        except (ValueError, OSError) as error:
            messagebox.showerror('Cannot prepare queue', str(error))
            return
        self.copy_button['state'] = 'normal'
        self.folder_button['state'] = 'normal'
        self.status.set('Batch ready. Copy the command, open Workspace > Console in Resolve, select Lua, paste and press Enter.')
        self.copy_command()

    def copy_command(self):
        command = 'dofile(' + lua_string((self.batch / 'queue.lua').as_posix()) + ')'
        self.root.clipboard_clear()
        self.root.clipboard_append(command)
        self.status.set('Command copied. In Resolve: Workspace > Console → Lua → paste → Enter. Then review the Deliver queue.')

    def open_folder(self):
        os.startfile(self.batch)

    def open_guide(self):
        os.startfile(resource_path('README.md'))

    def close(self):
        if self.busy:
            self.cancel.set()
            self.status.set('Cancelling the audio scan before closing…')
            self.root.after(100, self.close)
        else:
            self.root.destroy()


if __name__ == '__main__':
    App(tk.Tk()).root.mainloop()
