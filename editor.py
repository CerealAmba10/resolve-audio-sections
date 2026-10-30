"""Choose primary/background tracks, preview the cuts and pick which clips to render."""
import hashlib
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import detection as d
from updater import VERSION
from media import STILL_TYPES, Cancelled, still_video, track_envelope

ROLES = ('Primary', 'Background')
RULER, CLIP_LANE, LANE = 22, 30, 58
DB_MIN = -80.0
COLORS = dict(bg='#1c1d21', lane='#25272d', grid='#34363d', text='#c9ccd3', dim='#7c808a', primary='#4fb3ff',
              background='#5d616b', threshold='#ffb547', keep='#3ddc97', quiet='#a78bfa', short='#6b6f78', off='#a8443f', select='#ffffff')
PAUSE_ONLY = {'min_silence', 'min_burst', 'min_quiet'}
SETTINGS = [('min_silence', 'Split at silence of at least (s)', 0.1, 0.1, 600),
            ('padding', 'Padding around speech (s)', 0.05, 0, 10),
            ('min_burst', 'Ignore sounds shorter than (s)', 0.05, 0, 5),
            ('min_clip', 'Keep clips longer than (s)', 1, 0, 36000),
            ('max_length', 'Longest clip (s, 0 = no limit)', 10, 0, 36000),
            ('min_quiet', 'Also export quiet over (s, 0 = never)', 5, 0, 36000),
            ('min_dead', 'Cut out dead silence over (s, 0 = off)', 0.5, 0, 600),
            ('cut_window', 'Cut at quietest point in last (s)', 5, 1, 600),
            ('teaser', 'Teaser length (s)', 1, 1, 600),
            ('fade', 'Teaser fade-out (s)', 0.25, 0, 30),
            ('clip_fade', 'Fade out end of each clip (s)', 0.25, 0, 30)]


def clock(seconds):
    hours, rest = divmod(max(0.0, seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{int(hours)}:{int(minutes):02}:{seconds:05.2f}'


class Editor:
    def __init__(self, master, request, settings, on_render, on_cancel, send=None, pieces=None, defaults=None,
                 home=None, check_updates=None):
        self.request, self.settings = request, settings
        self.on_render, self.on_cancel = on_render, on_cancel
        timeline = request['timeline']
        self.fps = d.parse_fps(timeline['fps'])
        self.timeline = dict(fps=self.fps, start=int(timeline['start']), end=int(timeline['end']))
        self.duration = float((self.timeline['end'] - self.timeline['start']) / self.fps)
        self.untrusted = 0
        for track in timeline['audio']:
            for item in track['items']:
                item['source_seconds'], trusted = d.source_seconds(item, self.fps)
                self.untrusted += not trusted
        roles = settings.get('roles', {})
        self.tracks = [dict(track, label=f"A{track['index']}" + (f" · {track['name']}" if track['name'] else ''),
                            role=tk.StringVar(value=roles.get(track['name'] or f"A{track['index']}",
                                                              'Primary' if track['enabled'] else 'Background')),
                            threshold=tk.DoubleVar(value=-40.0), envelope=None, auto=None)
                       for track in timeline['audio']]
        self.sections, self.selected, self.edited = [], None, False
        # Review in Resolve: send() talks to the waiting script. `loaded` holds pieces read from a cuts
        # timeline (they replace detection until a setting changes); `target` is where they render from.
        self.send, self.defaults, self.quiet, self.busy = send, defaults or {}, False, False
        self.loaded = [dict(p) for p in pieces] if pieces else None
        self.cuts_name = request['timeline']['name'] if pieces else None
        self.target = None
        self.check_updates = check_updates
        self.home = Path(home) if home else Path.home() / 'Documents/Audio Sections Plugin'
        self.view = [0.0, max(self.duration, 1.0)]
        self.drag = None
        self.events, self.cancel = queue.Queue(), threading.Event()
        self.pending = None

        self.win = tk.Toplevel(master)
        self.win.title(f"Audio Sections · {timeline['name']}")
        self.win.geometry('1280x860')
        self.win.minsize(980, 640)
        self.win.protocol('WM_DELETE_WINDOW', self.close)
        self.build(timeline)
        self.win.lift()
        self.win.focus_force()
        for n in range(len(self.tracks)):
            threading.Thread(target=self.scan, args=(n,), daemon=True).start()
        self.win.after(100, self.poll)

    # ---------- layout ----------
    def build(self, timeline):
        outer = ttk.Frame(self.win, padding=14)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky='ew')
        ttk.Label(head, text=timeline['name'], font=('Segoe UI', 16, 'bold')).pack(side='left')
        videos = sum(1 for t in timeline['video'] if t['items'])
        ttk.Label(head, foreground=COLORS['dim'], text=f"   {clock(self.duration)} · {float(self.fps):g} fps · "
                  f"{len(self.tracks)} audio / {videos} video tracks · all tracks render as mixed in Resolve").pack(side='left')

        top = ttk.Frame(outer)
        top.grid(row=1, column=0, sticky='ew', pady=(10, 8))
        top.columnconfigure(0, weight=1)
        tracks = ttk.LabelFrame(top, text='Audio tracks: cut around Primary, ignore Background', padding=8)
        tracks.grid(row=0, column=0, sticky='nsew')
        for row, track in enumerate(self.tracks):
            ttk.Label(tracks, text=track['label'] + ('' if track['enabled'] else '  (off in Resolve)'),
                      width=28).grid(row=row, column=0, sticky='w')
            box = ttk.Combobox(tracks, textvariable=track['role'], values=ROLES, state='readonly', width=11)
            box.grid(row=row, column=1, padx=6, pady=2)
            ttk.Label(tracks, text='Silence below').grid(row=row, column=2, padx=(10, 4))
            ttk.Spinbox(tracks, textvariable=track['threshold'], from_=-90, to=-5, increment=1,
                        width=7).grid(row=row, column=3)
            ttk.Label(tracks, text='dBFS').grid(row=row, column=4, padx=(4, 8))
            ttk.Button(tracks, text='Auto', width=6, command=lambda t=track: self.auto(t)).grid(row=row, column=5)
            track['status'] = ttk.Label(tracks, text='scanning…', foreground=COLORS['dim'], width=16)
            track['status'].grid(row=row, column=6, padx=8)
        if not self.tracks:
            ttk.Label(tracks, text='This timeline has no audio tracks.').grid(row=0, column=0)

        options = ttk.LabelFrame(top, text='Cutting', padding=8)
        options.grid(row=0, column=1, sticky='nsew', padx=(10, 0))
        self.build_thumbnail_panel(top)
        self.values, self.pause_widgets = {}, []
        for row, (key, label, step, low, high) in enumerate(SETTINGS):
            self.values[key] = tk.DoubleVar(value=self.settings.get(key, d.DEFAULTS[key]))
            text = ttk.Label(options, text=label)
            text.grid(row=row, column=0, sticky='w')
            spin = ttk.Spinbox(options, textvariable=self.values[key], from_=low, to=high, increment=step, width=7)
            spin.grid(row=row, column=1, padx=(8, 0), pady=1)
            if key in PAUSE_ONLY:
                self.pause_widgets += [text, spin]
        self.pauses = tk.BooleanVar(value=self.settings.get('pauses', False))
        ttk.Checkbutton(options, text='Also split at pauses in Primary tracks', variable=self.pauses
                        ).grid(row=len(SETTINGS), column=0, columnspan=2, sticky='w', pady=(4, 0))
        # Re-cut whenever anything changes, however it was changed (typing, arrows, wheel, paste, drag).
        for var in [*self.values.values(), self.pauses, *(t[k] for t in self.tracks for k in ('role', 'threshold'))]:
            var.trace_add('write', lambda *args: self.changed())
        self.pauses.trace_add('write', lambda *args: self.show_mode())
        self.show_mode()

        self.canvas = tk.Canvas(outer, background=COLORS['bg'], highlightthickness=0, height=
                                RULER + CLIP_LANE + LANE * max(1, len(self.tracks)) + 8)
        self.canvas.grid(row=2, column=0, sticky='nsew')
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<ButtonPress-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.motion)
        self.canvas.bind('<ButtonRelease-1>', self.release)
        self.canvas.bind('<Motion>', self.hover)
        hint = ttk.Frame(outer)
        hint.grid(row=3, column=0, sticky='ew', pady=(4, 8))
        ttk.Label(hint, foreground=COLORS['dim'], text='Wheel: zoom · drag: scroll · drag clip edges to trim · '
                  'drag the orange line to set a track\'s silence level · click a clip to select').pack(side='left')
        ttk.Button(hint, text='Show all', command=self.fit).pack(side='right')
        if self.check_updates:
            ttk.Button(hint, text='Check for updates', command=self.check_updates).pack(side='right', padx=(0, 8))
        ttk.Label(hint, foreground=COLORS['dim'], text=f'Audio Sections {VERSION}').pack(side='right', padx=(0, 8))

        bottom = ttk.Frame(outer)
        bottom.grid(row=4, column=0, sticky='nsew')
        bottom.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(bottom, columns=('use', 'name', 'start', 'end', 'length', 'thumb'), show='headings',
                                  height=7, selectmode='browse')
        for column, label, width in [('use', 'Render', 60), ('name', 'Name', 200), ('start', 'Start', 95),
                                     ('end', 'End', 95), ('length', 'Length', 80), ('thumb', 'Thumbnail', 110)]:
            self.table.heading(column, text=label)
            self.table.column(column, width=width, anchor='w')
        self.table.grid(row=0, column=0, sticky='nsew')
        self.table.tag_configure('short', foreground=COLORS['dim'])
        self.table.bind('<<TreeviewSelect>>', self.table_selected)
        self.table.bind('<Double-1>', lambda e: self.toggle())
        self.table.bind('<space>', lambda e: self.toggle())
        bar = ttk.Scrollbar(bottom, orient='vertical', command=self.table.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=bar.set)

        out = ttk.LabelFrame(bottom, text='Output', padding=8)
        out.grid(row=0, column=2, sticky='nsew', padx=(10, 0))
        source = self.main_source()
        self.stem = tk.StringVar(value=self.defaults.get('stem') or (Path(source).stem if source else timeline['name']))
        self.folder = tk.StringVar(value=self.defaults.get('output') or (
            str(Path(source).parent / 'Audio Sections Exports') if source else str(Path.home() / 'Videos/Audio Sections Exports')))
        ttk.Label(out, text='File name').grid(row=0, column=0, sticky='w')
        ttk.Entry(out, textvariable=self.stem, width=34).grid(row=0, column=1, columnspan=2, sticky='ew', pady=2)
        ttk.Label(out, text='Folder').grid(row=1, column=0, sticky='w')
        ttk.Entry(out, textvariable=self.folder, width=28).grid(row=1, column=1, sticky='ew', pady=2)
        ttk.Button(out, text='…', width=3, command=self.browse).grid(row=1, column=2, padx=(4, 0))
        self.stem.trace_add('write', lambda *a: self.refresh_table())
        self.folder.trace_add('write', lambda *a: self.refresh_table())
        self.summary = tk.StringVar(value='Scanning audio…')
        ttk.Label(out, textvariable=self.summary, wraplength=300).grid(row=2, column=0, columnspan=3, sticky='w', pady=(8, 8))
        buttons = ttk.Frame(out)
        buttons.grid(row=3, column=0, columnspan=3, sticky='e')
        ttk.Button(buttons, text='Cancel', command=self.close).pack(side='left')
        self.cuts_button = ttk.Button(buttons, text='Make cuts in Resolve', state='disabled',
                                      command=lambda: self.render('cuts'))
        self.cuts_button.pack(side='left', padx=(8, 0))
        self.load_button = ttk.Button(buttons, text='Load edits from Resolve', state='disabled', command=self.load_edits)
        self.load_button.pack(side='left', padx=(8, 0))
        self.undo_button = ttk.Button(buttons, text='Undo cuts', state='disabled', command=self.undo_cuts)
        self.undo_button.pack(side='left', padx=(8, 0))
        self.render_button = ttk.Button(buttons, text='Render', command=self.render, state='disabled')
        self.render_button.pack(side='left', padx=(8, 0))
        self.status = tk.StringVar(value='')
        ttk.Label(outer, textvariable=self.status, foreground=COLORS['dim']).grid(row=5, column=0, sticky='w', pady=(6, 0))
        if self.loaded is not None:
            self.status.set(f'Showing the {len(self.loaded)} pieces of this cuts timeline as they are in Resolve. '
                            'Change any setting to cut automatically instead.')
        if self.untrusted:
            self.status.set(f'{self.untrusted} audio item(s) are retimed or report unusual source times; '
                            'their detection may be slightly offset.')

    def show_mode(self):
        """Grey out the settings that only apply when splitting at pauses."""
        for widget in self.pause_widgets:
            widget.state(['!disabled'] if self.pauses.get() else ['disabled'])

    def build_thumbnail_panel(self, top):
        """Background picture, titles file and how long the thumbnail opens each video (all clips, or per clip)."""
        panel = ttk.LabelFrame(top, text='Thumbnail', padding=8)
        panel.grid(row=0, column=2, sticky='nsew', padx=(10, 0))
        self.thumb_background = tk.StringVar(value=self.defaults.get('thumb_background', ''))
        self.thumb_titles = tk.StringVar(value=self.defaults.get('thumb_titles', ''))
        self.thumb_seconds = tk.DoubleVar(value=self.defaults.get('thumb_seconds', 1.0))
        self.thumb_full_label = tk.BooleanVar(value=self.defaults.get('thumb_full_label', False))
        self.title_font = tk.StringVar(value=self.defaults.get('title_font', ''))
        self.label_font = tk.StringVar(value=self.defaults.get('label_font', ''))
        fonts = [('Fonts', '*.ttf *.otf *.ttc')]
        for row, (label, var, types) in enumerate([('Background', self.thumb_background, [('Pictures', '*.png *.jpg *.jpeg')]),
                                                   ('Titles (.txt)', self.thumb_titles, [('Text', '*.txt')]),
                                                   ('Title font', self.title_font, fonts),
                                                   ('Label font', self.label_font, fonts)]):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky='w')
            ttk.Entry(panel, textvariable=var, width=22).grid(row=row, column=1, sticky='ew', pady=1)
            ttk.Button(panel, text='…', width=3, command=lambda v=var, t=types: self.pick_file(v, t)).grid(
                row=row, column=2, padx=(4, 0))
        ttk.Label(panel, text='Show at start of all clips (s)').grid(row=4, column=0, columnspan=2, sticky='w')
        ttk.Spinbox(panel, textvariable=self.thumb_seconds, from_=0, to=60, increment=0.5, width=6).grid(row=4, column=2)
        ttk.Checkbutton(panel, text='TEASER: N on full clips too', variable=self.thumb_full_label).grid(
            row=5, column=0, columnspan=3, sticky='w')
        self.clip_thumb = tk.DoubleVar(value=1.0)
        self.clip_thumb_label = ttk.Label(panel, text='Selected clip (s)', foreground=COLORS['dim'])
        self.clip_thumb_label.grid(row=6, column=0, columnspan=2, sticky='w')
        self.clip_thumb_box = ttk.Spinbox(panel, textvariable=self.clip_thumb, from_=0, to=60, increment=0.5, width=6,
                                          state='disabled')
        self.clip_thumb_box.grid(row=6, column=2)
        actions = ttk.Frame(panel)
        actions.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(4, 0))
        self.clip_thumb_reset = ttk.Button(actions, text='Use all-clips value', state='disabled', command=self.reset_clip_thumb)
        self.clip_thumb_reset.pack(side='left')
        ttk.Button(actions, text='Preview', command=self.preview_thumbnail).pack(side='right')
        self.clip_thumb.trace_add('write', lambda *a: self.set_clip_thumb())
        for var in (self.thumb_background, self.thumb_titles, self.thumb_seconds):
            var.trace_add('write', lambda *a: self.refresh_table())

    def pick_file(self, var, types):
        chosen = filedialog.askopenfilename(parent=self.win, filetypes=types + [('All files', '*.*')],
                                            initialdir=str(Path(var.get()).parent) if var.get() else None)
        if chosen:
            var.set(chosen)

    def thumb_default(self):
        try:
            return max(0.0, float(self.thumb_seconds.get()))
        except (tk.TclError, ValueError):
            return 1.0

    def clip_thumb_seconds(self, section):
        return section.get('thumb', self.thumb_default())

    def set_clip_thumb(self):
        """The per-clip spinbox: a value here overrides the all-clips value for the selected clip."""
        if self.quiet or self.selected is None or self.selected >= len(self.sections):
            return
        try:
            self.sections[self.selected]['thumb'] = max(0.0, float(self.clip_thumb.get()))
        except (tk.TclError, ValueError):
            return
        self.refresh_table()

    def reset_clip_thumb(self):
        if self.selected is not None and self.selected < len(self.sections):
            self.sections[self.selected].pop('thumb', None)
            self.show_clip_thumb()
            self.refresh_table()

    def show_clip_thumb(self):
        section = self.sections[self.selected] if self.selected is not None and self.selected < len(self.sections) else None
        self.quiet = True
        self.clip_thumb.set(self.clip_thumb_seconds(section) if section else self.thumb_default())
        self.quiet = False
        state = 'normal' if section else 'disabled'
        self.clip_thumb_box.configure(state=state)
        self.clip_thumb_reset.configure(state='normal' if section and 'thumb' in section else 'disabled')
        self.clip_thumb_label.configure(text=f'Clip at {clock(section["start"])} (s)' if section else 'Selected clip (s)',
                                        foreground='' if section else COLORS['dim'])

    def thumb_settings(self):
        """What the helper needs to draw thumbnails, or None when no background is chosen."""
        background = self.thumb_background.get().strip()
        if not background:
            return None
        return dict(background=background, titles=self.thumb_titles.get().strip(), seconds=self.thumb_default(),
                    full_label=bool(self.thumb_full_label.get()), title_font=self.title_font.get().strip(),
                    label_font=self.label_font.get().strip())

    def preview_thumbnail(self):
        thumb = self.thumb_settings()
        if not thumb or not Path(thumb['background']).is_file():
            return messagebox.showerror('Thumbnail', 'Choose a background picture first.', parent=self.win)
        try:
            from thumbnail import deal_titles, find_font, load_titles, render
            titles = load_titles(thumb['titles']) if thumb['titles'] else []
            title = (deal_titles(titles, 1) or ['Your title from the text file'])[0]
            width, height = int(self.request['timeline'].get('width') or 1920), int(self.request['timeline'].get('height') or 1080)
            target = self.home / 'thumbnail preview.png'
            target.parent.mkdir(parents=True, exist_ok=True)
            render(thumb['background'], title, 'TEASER: 1', width, height, thumb['title_font'] or None,
                   label_font=thumb['label_font'] or None).save(target)
            missing = [name for kind, name in (('title', 'Komika Axis'),) if not find_font(kind)[1]]
            if missing:
                self.status.set(f"{' and '.join(missing)} not installed on this computer, so a stand-in font is used.")
            os.startfile(target)
        except Exception as error:
            messagebox.showerror('Thumbnail', str(error), parent=self.win)

    def main_source(self):
        items = [i for t in self.tracks if t['role'].get() == 'Primary' for i in t['items'] if i.get('path')]
        items = items or [i for t in self.tracks for i in t['items'] if i.get('path')]
        return max(items, key=lambda i: i['end'] - i['start'])['path'] if items else None

    # ---------- scanning ----------
    def scan(self, n):
        # ponytail: one FFmpeg per track at once; queue them if a timeline ever has dozens of tracks.
        try:
            envelope = track_envelope(self.tracks[n], self.timeline, self.cancel,
                                      lambda f: self.events.put(('progress', n, f)))
            self.events.put(('track', n, envelope))
        except Cancelled:
            pass
        except Exception as error:
            self.events.put(('failed', n, str(error)))

    def poll(self):
        if not self.win.winfo_exists():
            return
        while not self.events.empty():
            kind, n, value = self.events.get_nowait()
            track = self.tracks[n]
            if kind == 'progress':
                track['status'].configure(text=f'scanning {value:.0%}')
            elif kind == 'track':
                track['envelope'], track['auto'] = value, d.auto_threshold(value)
                self.quiet = True                 # the automatic level is not a user change
                track['threshold'].set(track['auto'])
                self.quiet = False
                silent = max(value, default=d.FLOOR_DB) <= -90
                track['status'].configure(text='no sound found' if silent else f"auto {track['auto']:g} dB",
                                          foreground=COLORS['off'] if silent else COLORS['dim'])
                self.recompute()
            else:
                track['status'].configure(text='scan failed', foreground=COLORS['off'])
                self.win.after(0, lambda t=track['label'], v=value: messagebox.showerror(
                    'Audio scan failed', f'{t}:\n{v}', parent=self.win))
        if all(t['envelope'] is not None or 'failed' in str(t['status'].cget('text')) for t in self.tracks):
            busy = self.busy
            for button in (self.render_button, self.cuts_button):
                button.configure(state='normal' if self.included() and not busy else 'disabled')
            for button in (self.load_button, self.undo_button):
                button.configure(state='normal' if self.cuts_name and not busy else 'disabled')
        self.win.after(100, self.poll)

    def threshold(self, track):
        try:
            return float(track['threshold'].get())
        except (tk.TclError, ValueError):
            return track['auto'] if track['auto'] is not None else -40.0

    def auto(self, track):
        if track['auto'] is not None:
            track['threshold'].set(track['auto'])

    # ---------- detection ----------
    def setting(self, key):
        try:
            return max(0.0, float(self.values[key].get()))
        except (tk.TclError, ValueError):
            return d.DEFAULTS[key]

    def schedule(self):
        if self.pending:
            self.win.after_cancel(self.pending)
        self.pending = self.win.after(200, self.recompute)

    def recompute(self):
        self.pending = None
        primary = [t for t in self.tracks if t['envelope'] is not None and t['role'].get() == 'Primary']
        envelopes, thresholds = [t['envelope'] for t in primary], [self.threshold(t) for t in primary]
        audible = [t['envelope'] for t in self.tracks if t['envelope'] is not None and t['enabled']]
        min_length = self.setting('min_clip')
        if self.loaded is not None:
            self.sections = [dict(p, keep=True) for p in self.loaded]
        elif self.pauses.get():
            runs = d.active_runs(envelopes, thresholds)
            self.sections = d.find_sections(runs, self.duration, self.setting('min_silence'), self.setting('padding'),
                                            self.setting('min_burst'), min_length)
            self.sections = d.add_quiet(self.sections, self.duration, self.setting('min_quiet'))
            self.sections = d.cut_dead(self.sections, d.dead_spans(audible, self.setting('min_dead')), min_length)
            self.sections = d.split_long(self.sections, self.setting('max_length'), self.setting('cut_window'),
                                         envelopes, thresholds, min_length, runs, self.setting('padding'))
        else:
            self.sections = d.sound_clips(audible, envelopes, thresholds, self.duration, self.setting('min_dead'),
                                          min_length, self.setting('max_length'), self.setting('cut_window'),
                                          self.setting('padding'))
        for section in self.sections:
            section['use'] = section['keep']
        if self.edited and self.loaded is None:
            self.status.set('Settings changed, so manual trims were reset.')
        self.edited, self.selected = False, None
        self.refresh_table()
        self.draw()

    def included(self):
        return [s for s in self.sections if s['use'] and s['keep']]

    def first_number(self):
        taken = d.existing_names(self.folder.get()) + list(self.request.get('queued', []))
        return d.next_number(self.stem.get().strip(), taken)

    def refresh_table(self):
        self.table.delete(*self.table.get_children())
        number = self.first_number()
        for i, section in enumerate(self.sections):
            length = section['end'] - section['start']
            name = ''
            if section['use'] and section['keep']:
                marked = section.get('teaser_frames')
                teaser = f', teaser {float(marked / self.fps):.1f} s to marker' if marked else ''
                name, number = f'{self.stem.get().strip()} {number}  (+ {number}T{teaser})', number + 1
            use = ('✔ quiet' if section.get('quiet') else '✔') if section['use'] and section['keep'] else                 ('too short' if not section['keep'] else '—')
            shown = self.clip_thumb_seconds(section)
            thumb = ('—' if not self.thumb_settings() else 'cover only' if shown <= 0 else f'{shown:g} s') + \
                (' (this clip)' if 'thumb' in section and self.thumb_settings() else '')
            self.table.insert('', 'end', iid=str(i), values=(use, name, clock(section['start']),
                                                              clock(section['end']), f'{length:.2f} s', thumb),
                              tags=() if section['keep'] else ('short',))
        if self.selected is not None and self.selected < len(self.sections):
            self.table.selection_set(str(self.selected))
        chosen = self.included()
        total = sum(s['end'] - s['start'] for s in chosen)
        plural = '' if len(chosen) == 1 else 's'
        self.summary.set(f'{len(chosen)} clip{plural} + {len(chosen)} teaser{plural} · {clock(total)} total. '
                         f'Double-click a row to skip or include it.' if self.sections else 'No sound found on the primary tracks yet.')
        self.render_button.configure(text=f'Render {len(chosen)} clip{plural}' if chosen else 'Render')

    def toggle(self):
        if self.selected is not None and self.sections[self.selected]['keep']:
            self.sections[self.selected]['use'] ^= True
            self.refresh_table()
            self.draw()

    def table_selected(self, event):
        chosen = self.table.selection()
        if chosen and int(chosen[0]) != self.selected:
            self.selected = int(chosen[0])
            self.show_clip_thumb()
            section = self.sections[self.selected]
            margin = max(5.0, (section['end'] - section['start']) * 0.1)
            self.view = [max(0.0, section['start'] - margin), min(self.duration, section['end'] + margin)]
            self.draw()

    # ---------- drawing ----------
    def x(self, seconds):
        width = self.canvas.winfo_width()
        return (seconds - self.view[0]) / (self.view[1] - self.view[0]) * width

    def t(self, x):
        width = max(1, self.canvas.winfo_width())
        return self.view[0] + x / width * (self.view[1] - self.view[0])

    def lane(self):
        free = self.canvas.winfo_height() - RULER - CLIP_LANE - 4
        return max(LANE, min(160, free // max(1, len(self.tracks))))

    def lane_top(self, n):
        return RULER + CLIP_LANE + n * self.lane()

    def db_y(self, n, db):
        top = self.lane_top(n) + 4
        return top + (1 - (max(DB_MIN, min(0.0, db)) - DB_MIN) / -DB_MIN) * (self.lane() - 8)

    def draw(self):
        c = self.canvas
        c.delete('all')
        width, height = c.winfo_width(), c.winfo_height()
        span = self.view[1] - self.view[0]
        step = next(s for s in (0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200) if span / s <= width / 90)
        tick = (int(self.view[0] / step)) * step
        while tick <= self.view[1]:
            x = self.x(tick)
            c.create_line(x, RULER - 6, x, height, fill=COLORS['grid'])
            c.create_text(x + 3, 3, text=clock(tick)[:-3] if step >= 1 else clock(tick), anchor='nw',
                          fill=COLORS['dim'], font=('Segoe UI', 8))
            tick += step
        for n, track in enumerate(self.tracks):
            top = self.lane_top(n)
            c.create_rectangle(0, top + 2, width, top + self.lane() - 2, fill=COLORS['lane'], outline='')
        for i, s in enumerate(self.sections):
            if s['end'] < self.view[0] or s['start'] > self.view[1]:
                continue
            x0, x1 = self.x(s['start']), self.x(s['end'])
            color = COLORS['quiet' if s.get('quiet') else 'keep'] if s['keep'] and s['use'] else COLORS['off'] if s['keep'] else COLORS['short']
            c.create_rectangle(x0, RULER + 3, x1, RULER + CLIP_LANE - 3, fill=color, outline=COLORS['select']
                               if i == self.selected else '', width=2, stipple='' if s['keep'] else 'gray50')
            if s['keep'] and s['use']:
                marked = s.get('teaser_frames')
                teaser = s['start'] + float(marked / self.fps) if marked else min(s['end'], s['start'] + self.setting('teaser'))
                c.create_line(self.x(teaser), RULER + 3, self.x(teaser), RULER + CLIP_LANE - 3, fill=COLORS['bg'], dash=(2, 2))
        for n, track in enumerate(self.tracks):
            top = self.lane_top(n)
            primary = track['role'].get() == 'Primary'
            env = track['envelope']
            if env is not None and len(env):
                points, per = [], len(env) / self.duration * span / max(1, width)
                for px in range(0, width, 2):
                    a = int(self.t(px) / d.HOP)
                    b = max(a + 1, int(a + per * 2))
                    if a >= len(env) or b <= 0:
                        continue
                    points.append((px, self.db_y(n, max(env[max(0, a):b]))))
                if points:
                    base = top + self.lane() - 4
                    c.create_polygon([(points[0][0], base)] + points + [(points[-1][0], base)],
                                     fill=COLORS['primary'] if primary else COLORS['background'], outline='')
                if primary:
                    y = self.db_y(n, self.threshold(track))
                    c.create_line(0, y, width, y, fill=COLORS['threshold'], dash=(4, 3), width=2)
            c.create_text(6, top + 5, anchor='nw', fill=COLORS['text'], font=('Segoe UI', 9, 'bold'),
                          text=track['label'] + ('' if primary else '  (ignored)'))
        for s in self.sections:
            if s['keep'] and s['use']:
                for edge in (s['start'], s['end']):
                    c.create_line(self.x(edge), RULER + CLIP_LANE, self.x(edge), height, fill=COLORS['keep'], width=2)

    # ---------- mouse ----------
    def target(self, x, y):
        if RULER <= y < RULER + CLIP_LANE:
            for i, s in enumerate(self.sections):
                for edge in ('start', 'end'):
                    if abs(self.x(s[edge]) - x) <= 6:
                        return ('edge', i, edge)
        n = int((y - RULER - CLIP_LANE) // self.lane()) if y >= RULER + CLIP_LANE else -1
        if 0 <= n < len(self.tracks) and self.tracks[n]['role'].get() == 'Primary' and self.tracks[n]['envelope'] is not None:
            if abs(self.db_y(n, self.threshold(self.tracks[n])) - y) <= 6:
                return ('threshold', n, None)
        return ('pan', None, None)

    def hover(self, event):
        kind = self.target(event.x, event.y)[0]
        self.canvas.configure(cursor={'edge': 'sb_h_double_arrow', 'threshold': 'sb_v_double_arrow'}.get(kind, 'fleur'))

    def press(self, event):
        self.drag = self.target(event.x, event.y) + (event.x, list(self.view), False)

    def motion(self, event):
        if not self.drag:
            return
        kind, a, b, x0, view, _ = self.drag
        self.drag = self.drag[:5] + (True,)
        if kind == 'pan':
            shift = (x0 - event.x) / max(1, self.canvas.winfo_width()) * (view[1] - view[0])
            shift = max(-view[0], min(self.duration - view[1], shift))
            self.view = [view[0] + shift, view[1] + shift]
        elif kind == 'threshold':
            top = self.lane_top(a) + 4
            db = DB_MIN * ((event.y - top) / (self.lane() - 8))
            self.tracks[a]['threshold'].set(round(max(DB_MIN, min(0.0, db)), 1))
        else:
            s = self.sections[a]
            seconds = round(self.t(event.x) * float(self.fps)) / float(self.fps)
            low = self.sections[a - 1]['end'] if b == 'start' and a > 0 else 0.0
            high = self.sections[a + 1]['start'] if b == 'end' and a + 1 < len(self.sections) else self.duration
            cap = self.setting('max_length') or self.duration
            if b == 'start':
                s['start'] = max(low, s['end'] - cap, min(s['end'] - 1, seconds))
            else:
                s['end'] = min(high, s['start'] + cap, max(s['start'] + 1, seconds))
            s['keep'] = s['end'] - s['start'] > self.setting('min_clip')
            s.pop('frames', None)
            s.pop('teaser_frames', None)
            s['use'] = s['use'] or s['keep']
            self.edited, self.selected = True, a
            self.refresh_table()
        self.draw()

    def release(self, event):
        if self.drag and self.drag[0] == 'pan' and not self.drag[5] and RULER <= event.y < RULER + CLIP_LANE:
            seconds = self.t(event.x)
            hit = [i for i, s in enumerate(self.sections) if s['start'] <= seconds <= s['end']]
            if hit:
                self.selected = hit[0]
                self.table.selection_set(str(hit[0]))
                self.table.see(str(hit[0]))
                self.draw()
        self.drag = None

    def wheel(self, event):
        anchor = self.t(event.x)
        scale = 0.8 if event.delta > 0 else 1.25
        span = min(self.duration, max(2.0, (self.view[1] - self.view[0]) * scale))
        start = max(0.0, min(self.duration - span, anchor - (anchor - self.view[0]) * span / (self.view[1] - self.view[0])))
        self.view = [start, start + span]
        self.draw()

    def fit(self):
        self.view = [0.0, max(self.duration, 1.0)]
        self.draw()

    # ---------- finish ----------
    def browse(self):
        folder = filedialog.askdirectory(parent=self.win, initialdir=self.folder.get())
        if folder:
            self.folder.set(folder)

    def render(self, action='render'):
        """Render the chosen clips, or (action 'cuts') lay them out on a review timeline in Resolve."""
        stem, folder = self.stem.get().strip(), Path(self.folder.get().strip())
        if not stem or any(ch in stem for ch in '\\/:*?"<>|'):
            return messagebox.showerror('File name', 'Enter a file name without \\ / : * ? " < > |', parent=self.win)
        if not folder.is_absolute():
            return messagebox.showerror('Folder', 'Choose an output folder.', parent=self.win)
        thumb = self.thumb_settings()
        for label, path in (('background picture', thumb and thumb['background']), ('titles file', thumb and thumb['titles']),
                            ('title font', thumb and thumb['title_font']), ('label font', thumb and thumb['label_font'])):
            if path and not Path(path).is_file():
                return messagebox.showerror('Thumbnail', f'The {label} was not found:\n{path}', parent=self.win)
        teaser_frames = round(self.setting('teaser') * float(self.fps))
        jobs, number = [], self.first_number()
        for s in self.included():
            first, last = s.get('frames') or d.to_frames(s['start'], s['end'], self.fps)
            name = f'{stem} {number}'
            for output in (name, name + 'T'):
                if (folder / (output + '.mp4')).exists():
                    return messagebox.showerror('Already exists', f'{output}.mp4 already exists in that folder.', parent=self.win)
            base = self.target['start'] if self.target else self.timeline['start']
            jobs.append(dict(name=name, teaser=name + 'T', mark_in=base + first, thumb_seconds=self.clip_thumb_seconds(s),
                             mark_out=base + last, frames=last - first + 1, first=first, last=last,
                             # a marker on the cuts timeline ends the teaser; else the setting (short clip: whole clip)
                             teaser_frames=s.get('teaser_frames') or min(teaser_frames, last - first + 1),
                             start=s['start'], end=s['end']))
            number += 1
        if not jobs:
            return
        folder.mkdir(parents=True, exist_ok=True)
        if action == 'cuts':
            self.busy = True
            clips = [dict(first=j['first'], last=j['last']) for j in jobs]
            record = dict(stem=stem, output=str(folder), teaser_frames=teaser_frames,
                          fade=self.setting('fade'), clip_fade=self.setting('clip_fade'))
            threading.Thread(target=self.prepare_stills, args=(clips, teaser_frames, record), daemon=True).start()
            return
        roles = dict(self.settings.get('roles', {}))
        roles.update({t['name'] or f"A{t['index']}": t['role'].get() for t in self.tracks})
        self.settings.update({key: self.setting(key) for key in d.DEFAULTS}, roles=roles, pauses=self.pauses.get())
        self.cancel.set()
        self.win.destroy()
        self.on_render(dict(stem=stem, output=str(folder), fps=str(self.fps), teaser_frames=teaser_frames,
                            thumb=self.thumb_settings(),
                            fade=self.setting('fade'), clip_fade=self.setting('clip_fade'), jobs=jobs,
                            timeline=self.target['name'] if self.target else None,
                            tracks={t['label']: dict(role=t['role'].get(), threshold=self.threshold(t))
                                    for t in self.tracks}))

    def changed(self):
        """A setting, role or level changed: go back to automatic cuts on the scanned timeline."""
        if self.quiet:
            return
        if self.loaded is not None:
            self.status.set('Settings changed: showing automatic cuts again. Load edits to get your Resolve pieces back.')
        self.loaded = self.target = None
        self.schedule()

    def prepare_stills(self, clips, teaser_frames, record):
        """Turn still images on video tracks into videos of their item's length (cached), then ask Resolve
        to build the cuts timeline with them, so a still is not cut down to a single frame."""
        try:
            timeline, stills = self.request['timeline'], {}
            width, height = int(timeline.get('width') or 1920), int(timeline.get('height') or 1080)
            items = [(t['index'], i) for t in timeline['video'] for i in t['items']
                     if i.get('path') and Path(i['path']).suffix.lower() in STILL_TYPES]
            for n, (index, item) in enumerate(items, 1):
                self.win.after(0, self.status.set, f'Preparing still image {n} of {len(items)} for the cuts timeline…')
                frames = int(item['end'] - item['start'])
                path = Path(item['path'])
                stat = path.stat()
                key = hashlib.sha1(f'{path}|{stat.st_mtime_ns}|{stat.st_size}|{frames}|{self.fps}|{width}x{height}'
                                   .encode()).hexdigest()[:16]
                video = still_video(path, frames, self.fps, width, height, self.home / 'stills' / f'{path.stem} {key}.mp4')
                stills[f"video{index}:{int(item['start'])}"] = video.as_posix()
            command = dict(action='cuts', teaser=teaser_frames, clips=clips, **({'stills': stills} if stills else {}))
            self.win.after(0, self.status.set, 'Making the cuts timeline in Resolve…')
            self.win.after(0, lambda: self.send(command, record=record))
        except Exception as error:
            self.win.after(0, self.on_result, dict(status='command_error', message=str(error)))

    def undo_cuts(self):
        self.busy = True
        self.status.set(f'Deleting "{self.cuts_name}" from Resolve…')
        self.send(dict(action='undo', timeline=self.cuts_name))

    def load_edits(self):
        self.busy = True
        self.status.set(f'Reading "{self.cuts_name}" from Resolve…')
        self.send(dict(action='pieces', timeline=self.cuts_name))

    def on_result(self, result):
        """A reply from Resolve to Make cuts or Load edits."""
        if not self.win.winfo_exists():
            return
        self.busy = False
        if result['status'] == 'cuts_ready':
            self.cuts_name = result['timeline']
            self.status.set(f'Made "{self.cuts_name}" in Resolve. Edit it there (blade to cut, delete a through edit '
                            'to merge, delete a piece to skip it), then click Load edits from Resolve.')
        elif result['status'] == 'undone':
            if result.get('ended'):          # the window was opened on that cuts timeline: nothing left to render
                return self.close_quietly()
            self.cuts_name, self.loaded, self.target = None, None, None
            self.recompute()
            self.status.set(f'Deleted "{result["timeline"]}" and went back to your timeline. Showing automatic cuts.')
        elif result['status'] == 'pieces':
            start = int(result['start'])
            self.loaded = d.pieces_from(result['pieces'], result.get('markers') or [], start, self.fps)
            self.target = dict(name=result['timeline'], start=start)
            self.recompute()
            marked = sum(1 for p in self.loaded if p['teaser_frames'])
            self.status.set(f'Loaded {len(self.loaded)} pieces from "{result["timeline"]}"'
                            + (f', {marked} with a marker ending the teaser' if marked else '')
                            + '. Render renders them from that timeline. Changing a setting goes back to automatic cuts.')
        else:
            self.status.set('')
            message = result.get('message', 'Resolve could not do that.')
            self.status.set('Resolve: ' + message)
            # Shown after the helper's loop returns, so an open dialog never holds it up.
            self.win.after(0, lambda: messagebox.showerror('Resolve', message, parent=self.win))

    def close_quietly(self):
        self.cancel.set()
        if self.win.winfo_exists():
            self.win.destroy()

    def close(self):
        self.cancel.set()
        self.win.destroy()
        self.on_cancel()
