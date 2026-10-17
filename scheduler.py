"""Post planner for exported clips. Plans slots, freezes each site's launch options into the post, and
exports batches: the Post Helper Firefox extension fills them into the site, and the creator clicks Post."""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

FMT = '%Y-%m-%d %H:%M'
HOME = Path.home() / 'Documents' / 'Audio Sections Scheduler'
COMMON = dict(enabled=False, clip='teaser', max_seconds=0, max_mb=0, vertical=False, nsfw=True,
              times='12:00', caption='{name} {tags}', tags='', url='')
# Every value is editable per platform in Settings and copied into each post when it is planned.
# ponytail: limits/option names are best-known values; confirm against each site's real upload page.
DEFAULTS = {
    'Fansly': {**COMMON, 'enabled': True, 'clip': 'full', 'times': '12:00, 20:00', 'url': 'https://fansly.com/',
               'audience': 'subscribers', 'price': 0.0},
    'RedGifs': {**COMMON, 'enabled': True, 'max_seconds': 60, 'times': '14:00', 'caption': '{name}',
                'url': 'https://www.redgifs.com/', 'niches': '', 'keep_audio': True, 'public': True},
    'Pornhub Shorts': {**COMMON, 'enabled': True, 'max_seconds': 60, 'vertical': True, 'times': '16:00',
                       'caption': '{name}', 'url': 'https://www.pornhub.com/', 'categories': '', 'private': False},
    'Instagram': {**COMMON, 'max_seconds': 180, 'vertical': True, 'nsfw': False, 'times': '18:00',
                  'url': 'https://www.instagram.com/'},
    'TikTok': {**COMMON, 'max_seconds': 600, 'vertical': True, 'nsfw': False, 'times': '17:00',
               'url': 'https://www.tiktok.com/tiktokstudio/upload'},
    'Reddit': {**COMMON, 'max_seconds': 900, 'max_mb': 1024, 'times': '21:00', 'caption': '{name}',
               'url': 'https://www.reddit.com/submit', 'subreddit': '', 'flair': ''},
}
# Sites the Post Helper extension fills in (the creator always clicks Post). Instagram, TikTok and Reddit forbid
# unapproved automation, so their batches are for manual upload or the site's own scheduler.
HELPER_SITES = ('Fansly', 'RedGifs', 'Pornhub Shorts')
# planned → exported → posted
EDITABLE = ('planned', 'exported')


def connect(home=HOME):
    Path(home).mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(home) / 'queue.db', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, platform TEXT, file TEXT, at TEXT,
        caption TEXT, options TEXT, status TEXT DEFAULT 'planned', UNIQUE(platform, file))""")
    return db


def clip_kind(path):
    return 'teaser' if re.search(r' \d+T$', Path(path).stem) else 'full'


def problems(settings, path, info, size):
    """Reasons a clip does not fit a platform. info: probe_media dict or None."""
    found = []
    if settings['clip'] != 'any' and clip_kind(path) != settings['clip']:
        found.append(f"not a {settings['clip']} clip")
    if info is None:
        return found + ['cannot read media']
    if settings['max_seconds'] and info['duration'] > settings['max_seconds'] + 0.5:
        found.append(f"{info['duration']:.0f}s > {settings['max_seconds']}s")
    if settings['max_mb'] and size > settings['max_mb'] * 1024 * 1024:
        found.append(f"{size / 1048576:.0f} MB > {settings['max_mb']} MB")
    if settings['vertical'] and info['width'] >= info['height']:
        found.append(f"{info['width']}x{info['height']} is not vertical")
    return found


def probe_media(path):
    from ffmpeg_tools import tool_path
    result = subprocess.run([str(tool_path('ffprobe', Path(__file__).parent)), '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=width,height:format=duration', '-of', 'json', str(path)],
                            capture_output=True, text=True, timeout=60, check=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    return dict(duration=float(data['format']['duration']), width=stream['width'], height=stream['height'])


def next_slots(times, start, taken):
    """Yield free datetimes after start at the platform's daily times."""
    clock = sorted(datetime.strptime(t.strip(), '%H:%M').time() for t in times.split(',') if t.strip())
    if not clock:
        raise ValueError('Add at least one posting time, e.g. 12:00')
    day = start.date()
    while True:
        for moment in clock:
            slot = datetime.combine(day, moment)
            if slot > start and slot.strftime(FMT) not in taken:
                yield slot
        day += timedelta(days=1)


def plan(files, settings, schedule, start, probe):
    """Return (new posts, skipped messages) for every enabled platform."""
    new, skipped = [], []
    for platform, conf in settings.items():
        if not conf['enabled']:
            continue
        taken = {p['at'] for p in schedule + new if p['platform'] == platform}
        done = {p['file'] for p in schedule + new if p['platform'] == platform}
        slots = next_slots(conf['times'], start, taken)
        for path in files:
            if str(path) in done:
                continue
            bad = problems(conf, path, probe(path), Path(path).stat().st_size)
            if bad:
                skipped.append(f'{platform}: {Path(path).name} — ' + ', '.join(bad))
                continue
            text = conf['caption'].format(name=Path(path).stem, tags=conf['tags']).strip()
            new.append(dict(platform=platform, file=str(path), at=next(slots).strftime(FMT),
                            caption=text, options=json.dumps(conf)))
    return new, skipped


def save_posts(db, posts):
    db.executemany('INSERT INTO posts(platform, file, at, caption, options) VALUES(:platform, :file, :at, :caption, :options)', posts)
    db.commit()


def stage(posts, root):
    """Link/copy posts into one folder per platform with batch.json (for the Post Helper) and captions.txt."""
    folders, batches = {}, {}
    for post in sorted(posts, key=lambda p: p['at']):
        folder = folders.setdefault(post['platform'], Path(root) / f"{post['platform']} {datetime.now():%Y-%m-%d %H%M}")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / Path(post['file']).name
        if not target.exists():
            try:
                os.link(post['file'], target)  # no extra disk space on the same drive
            except OSError:
                shutil.copy2(post['file'], target)
        nsfw = json.loads(post['options'])['nsfw']
        with (folder / 'captions.txt').open('a', encoding='utf-8') as out:
            out.write(f"{post['at']} | {target.name} | {'NSFW' if nsfw else 'SFW'}\n{post['caption']}\n\n")
        batches.setdefault(folder, []).append(dict(platform=post['platform'], file=target.name, at=post['at'],
                                                   caption=post['caption'], options=json.loads(post['options'])))
    for folder, batch in batches.items():
        (folder / 'batch.json').write_text(json.dumps(batch, indent=2), encoding='utf-8')
    return folders


class Scheduler:
    def __init__(self, root, home=None):
        self.root, self.home = root, Path(home or HOME)
        self.db = connect(self.home)
        saved = self.load_settings()
        self.settings = {name: {**conf, **{k: v for k, v in saved.get(name, {}).items() if k in conf}}
                         for name, conf in DEFAULTS.items()}
        self.files = []
        root.title('Audio Sections · Post Scheduler')
        root.geometry('1100x720')
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill='both', expand=True)
        bar = ttk.Frame(outer)
        bar.pack(fill='x')
        ttk.Button(bar, text='Add clips…', command=self.add_clips).pack(side='left')
        self.enabled = {}
        for name in self.settings:
            var = tk.BooleanVar(value=self.settings[name]['enabled'])
            var.trace_add('write', lambda *_, n=name, v=var: self.toggle(n, v))
            self.enabled[name] = var
            ttk.Checkbutton(bar, text=name, variable=var).pack(side='left', padx=4)
        ttk.Button(bar, text='Settings…', command=self.edit_settings).pack(side='right')
        self.clips = ttk.Treeview(outer, columns=('kind',), height=7, selectmode='extended')
        self.clips.heading('#0', text='Clips (select, then Plan)')
        self.clips.heading('kind', text='Type')
        self.clips.pack(fill='x', pady=8)
        ttk.Button(outer, text='Plan selected clips', command=self.plan_selected).pack(anchor='w')
        cols = ('at', 'platform', 'file', 'status', 'caption')
        self.table = ttk.Treeview(outer, columns=cols, show='headings', selectmode='extended')
        for col, width in zip(cols, (120, 110, 260, 80, 480)):
            self.table.heading(col, text='When' if col == 'at' else col.title())
            self.table.column(col, width=width)
        self.table.pack(fill='both', expand=True, pady=8)
        actions = ttk.Frame(outer)
        actions.pack(fill='x')
        for text, command in [('Export batch', self.export_selected),
                              ('Mark posted', self.mark_posted), ('Remove', self.remove), ('Refresh', self.refresh)]:
            ttk.Button(actions, text=text, command=command).pack(side='left', padx=(0, 8))
        self.refresh()

    def load_settings(self):
        try:
            return json.loads((self.home / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        temp = self.home / 'settings.json.tmp'
        temp.write_text(json.dumps(self.settings, indent=2), encoding='utf-8')
        os.replace(temp, self.home / 'settings.json')

    def toggle(self, name, var):
        self.settings[name]['enabled'] = var.get()
        self.save_settings()

    def posts(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM posts ORDER BY at')]

    def refresh(self):
        self.table.delete(*self.table.get_children())
        for post in self.posts():
            self.table.insert('', 'end', iid=str(post['id']), values=(
                post['at'], post['platform'], Path(post['file']).name, post['status'], post['caption']))

    def selected(self, statuses=None):
        ids = [int(i) for i in self.table.selection()]
        return [p for p in self.posts() if p['id'] in ids and (statuses is None or p['status'] in statuses)]

    def set_status(self, posts, status, **extra):
        fields = dict(status=status, **extra)
        sql = ', '.join(f'{k}=?' for k in fields)
        self.db.executemany(f'UPDATE posts SET {sql} WHERE id=?', [(*fields.values(), p['id']) for p in posts])
        self.db.commit()
        self.refresh()

    def add_clips(self):
        for path in filedialog.askopenfilenames(filetypes=[('Video', '*.mp4 *.mov *.mkv')]):
            if path not in self.files:
                self.files.append(path)
                self.clips.insert('', 'end', iid=path, text=Path(path).name, values=(clip_kind(path),))

    def plan_selected(self):
        cache = {}

        def probe(path):
            if path not in cache:
                try:
                    cache[path] = probe_media(path)
                except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
                    cache[path] = None
            return cache[path]
        try:
            new, skipped = plan(list(self.clips.selection()), self.settings, self.posts(), datetime.now(), probe)
        except ValueError as error:
            return messagebox.showerror('Scheduler', str(error))
        save_posts(self.db, new)
        self.refresh()
        message = f'Planned {len(new)} posts. Review them, then Export batch.'
        if skipped:
            message += '\n\nSkipped:\n' + '\n'.join(skipped[:30])
        messagebox.showinfo('Scheduler', message)

    def export_selected(self):
        posts = self.selected(EDITABLE)
        if not posts:
            return messagebox.showinfo('Scheduler', 'Select planned posts first.')
        folders = stage(posts, self.home / 'Upload Batches')
        self.set_status(posts, 'exported')
        for platform, folder in folders.items():
            os.startfile(folder)
            if platform not in HELPER_SITES:
                webbrowser.open(self.settings[platform]['url'])  # upload by hand or with the site's own scheduler
        messagebox.showinfo('Scheduler', 'Batches exported. For Fansly, RedGifs and Pornhub, open Post Helper in Firefox '
                            'and import the batch folder. Upload the others by hand or with their own scheduler.')

    def mark_posted(self):
        self.set_status(self.selected(), 'posted')

    def remove(self):
        self.db.executemany('DELETE FROM posts WHERE id=?', [(p['id'],) for p in self.selected()])
        self.db.commit()
        self.refresh()

    def edit_settings(self):
        window = tk.Toplevel(self.root)
        window.title('Platform settings')
        name = tk.StringVar(value=next(iter(self.settings)))
        ttk.Combobox(window, textvariable=name, values=list(self.settings), state='readonly').pack(fill='x', padx=12, pady=8)
        form = ttk.Frame(window)
        form.pack(fill='both', padx=12)
        hints = dict(clip='teaser, full or any', max_seconds='0 = no limit', max_mb='0 = no limit',
                     times='daily, comma separated HH:MM', caption='uses {name} and {tags}',
                     audience='subscribers, followers or public', price='0 = free')
        vars_ = {}

        def show(*_):
            for child in form.winfo_children():
                child.destroy()
            vars_.clear()
            for row, (key, value) in enumerate(self.settings[name.get()].items()):
                if key == 'enabled':
                    continue
                vars_[key] = tk.StringVar(value=value)
                ttk.Label(form, text=key.replace('_', ' ')).grid(row=row, column=0, sticky='w')
                ttk.Entry(form, textvariable=vars_[key], width=50).grid(row=row, column=1, padx=12, pady=3)
                ttk.Label(form, text=hints.get(key, '')).grid(row=row, column=2, sticky='w')

        def apply():
            conf = dict(self.settings[name.get()])
            try:
                for key, var in vars_.items():
                    raw = var.get().strip()
                    kind = type(DEFAULTS[name.get()][key])
                    conf[key] = raw.lower() in ('1', 'true', 'yes') if kind is bool else kind(raw)
                if conf['clip'] not in ('teaser', 'full', 'any'):
                    raise ValueError('clip must be teaser, full or any')
                next(next_slots(conf['times'], datetime.now(), set()))
                conf['caption'].format(name='', tags='')
            except (ValueError, KeyError, IndexError) as error:
                return messagebox.showerror('Settings', str(error), parent=window)
            self.settings[name.get()] = conf
            self.save_settings()
            messagebox.showinfo('Settings', 'Saved. Applies to posts planned from now on.', parent=window)
        name.trace_add('write', show)
        show()
        ttk.Button(window, text='Save', command=apply).pack(anchor='e', padx=12, pady=10)


if __name__ == '__main__':
    root = tk.Tk()
    Scheduler(root)
    root.mainloop()
