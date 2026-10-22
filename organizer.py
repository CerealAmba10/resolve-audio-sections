"""Ducky’s Clip Organizer: local app serving the library dashboard at http://127.0.0.1:47833 and the clip picker
data Post Helper uses. Everything stays on this computer; the only outside request is the GitHub
update check."""
import hashlib
import json
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import library

VERSION = '1.1.1'
PORT = 47833
REPO = 'CerealAmba10/resolve-audio-sections'
ASSET = 'DuckysClipOrganizer.exe'
HELPER_SITES = ('Fansly', 'RedGifs', 'Pornhub Shorts')
UI = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'ui'
STATIC = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
          '/app.css': ('app.css', 'text/css'), '/icon.svg': ('icon.svg', 'image/svg+xml')}
HEADER = 'X-Clip-Organizer'  # sites can't send custom headers here without a CORS preflight, which is refused


def folder_dialog():
    """The modern Windows "Select folder" dialog (address bar, typed paths), on top of everything.
    It runs in a short-lived copy of this program, so the dialog can never freeze the app."""
    args = [sys.executable, '--pick-folder'] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__)), '--pick-folder']
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.stdout.strip() or None


def pick_folder():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)  # open in front of the app window, not behind it
    folder = filedialog.askdirectory(parent=root, title='Choose a clip folder', mustexist=True)
    root.destroy()
    sys.stdout.write(str(Path(folder)) if folder else '')


TAG = 'organizer-v'  # this app's release tags; the repo's plain vX.Y releases are Audio Sections


def version_tuple(text):
    return tuple(int(n) for n in text.split('-v')[-1].lstrip('v').split('.') if n.isdigit())


def latest_release():
    """The newest Ducky's Clip Organizer release (tagged organizer-vX.Y.Z, with the exe attached)."""
    request = urllib.request.Request(f'https://api.github.com/repos/{REPO}/releases?per_page=50',
                                     headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'ClipOrganizer'})
    with urllib.request.urlopen(request, timeout=10) as response:
        releases = json.load(response)
    ours = [r for r in releases if r.get('tag_name', '').startswith(TAG) and not r.get('draft') and not r.get('prerelease')
            and any(a['name'] == ASSET for a in r.get('assets', []))]
    if not ours:
        return dict(version=VERSION, notes='', asset=None, available=False)
    release = max(ours, key=lambda r: version_tuple(r['tag_name']))
    asset = next(a for a in release['assets'] if a['name'] == ASSET)
    version = release['tag_name'][len(TAG):]
    return dict(version=version, notes=release.get('body') or '', asset=asset,
                available=version_tuple(version) > version_tuple(VERSION))


def install_update(release, server):
    """Download the new exe, check it against GitHub's SHA-256, swap it in and restart."""
    if not getattr(sys, 'frozen', False):
        raise ValueError('Updates install into the packaged app only. Use git pull for the source version.')
    digest = (release['asset'] or {}).get('digest') or ''
    if not digest.startswith('sha256:'):
        raise ValueError('This release has no checksum, so it was not installed.')
    exe = Path(sys.executable)
    new = exe.with_suffix('.new')
    request = urllib.request.Request(release['asset']['browser_download_url'], headers={'User-Agent': 'ClipOrganizer'})
    sha = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=300) as response, new.open('wb') as out:
        for block in iter(lambda: response.read(1 << 20), b''):
            sha.update(block)
            out.write(block)
    if sha.hexdigest() != digest.split(':', 1)[1]:
        new.unlink()
        raise ValueError('The download did not match its checksum, so it was not installed.')
    os.replace(exe, exe.with_suffix('.old'))  # a running exe can be renamed, not overwritten
    os.replace(new, exe)

    def restart():
        time.sleep(0.5)
        server.shutdown()
        server.server_close()
        # A fresh start: without this the new exe reuses this copy's unpacked files, which are deleted as it exits.
        subprocess.Popen([str(exe)], env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
                         creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
        os._exit(0)
    threading.Thread(target=restart, daemon=True).start()


class App:
    """State shared by request handlers; one database connection guarded by a lock."""

    def __init__(self, home=library.DATA, probe=None):
        self.home = Path(home)
        self.db = library.connect(self.home)
        library.ensure_presets(self.db)
        self.lock = threading.RLock()
        self.probe = probe
        self.release = None
        self.window = None  # the app window, once open
        self.helper_seen = None
        self.ffmpeg = 'ready' if ffmpeg_ready() else 'missing'

    def clip_rows(self, folder):
        used = library.last_used(self.db)
        counts = {}
        for row in self.db.execute('SELECT fp, platform, COUNT(*) n, MAX(at) last FROM posts GROUP BY fp, platform'):
            counts.setdefault(row['fp'], {})[row['platform']] = {'count': row['n'], 'last': row['last']}
        recent = datetime.now() - timedelta(days=14)
        rows = []
        teasers = {r['parent']: r['name'] for r in self.db.execute(
            'SELECT parent, name FROM clips WHERE folder=? AND missing=0 AND parent IS NOT NULL', (folder,))}
        for c in self.db.execute('SELECT * FROM clips WHERE folder=? AND missing=0 AND parent IS NULL ORDER BY mtime, name',
                                 (folder,)):
            last = used.get(c['fp'])
            rows.append(dict(fp=c['fp'], name=c['name'], size=c['size'], posts=counts.get(c['fp'], {}),
                             teaser=teasers.get(c['fp']),
                             last=last.strftime(library.FMT) if last else None,
                             status='fresh' if not last else 'recent' if last > recent else 'rested'))
        return rows

    def state(self):
        folders = []
        for f in self.db.execute('SELECT * FROM folders ORDER BY added'):
            rows = self.clip_rows(f['path'])
            folders.append(dict(path=f['path'], name=Path(f['path']).name, prefix=f['prefix'], count=len(rows),
                                fresh=sum(r['status'] == 'fresh' for r in rows), autosort=bool(f['autosort'])))
        seen = self.helper_seen and (datetime.now() - self.helper_seen).total_seconds() < 600
        return dict(version=VERSION, folders=folders, helper=bool(seen), release=self.release, ffmpeg=self.ffmpeg)

    def detail(self, fp):
        clip = self.db.execute('SELECT * FROM clips WHERE fp=?', (fp,)).fetchone()
        if not clip:
            return None
        posts = [dict(r) for r in self.db.execute('SELECT platform, at, source FROM posts WHERE fp=? ORDER BY at DESC', (fp,))]
        batches = [dict(r) for r in self.db.execute("""SELECT b.path, b.created FROM batch_items i JOIN batches b
            ON b.id=i.batch WHERE i.fp=? ORDER BY b.created DESC""", (fp,))]
        planned = [dict(r) for r in self.db.execute('SELECT platform, at, done FROM planned WHERE fp=? ORDER BY at', (fp,))]
        totals, site_posts = library.clip_totals(self.db, fp)
        return dict(fp=fp, name=clip['name'], folder=clip['folder'], size=clip['size'], posts=posts, batches=batches,
                    planned=planned, totals=totals, site_posts=site_posts)

    def activity(self):
        today = datetime.now().date()
        start = today - timedelta(days=today.weekday() + 7 * 11)
        weeks = [dict(week=(start + timedelta(weeks=i)).isoformat(), platforms={}) for i in range(12)]
        for row in self.db.execute('SELECT platform, at FROM posts WHERE at >= ?', (start.isoformat(),)):
            index = (datetime.strptime(row['at'], library.FMT).date() - start).days // 7
            if 0 <= index < 12:
                weeks[index]['platforms'][row['platform']] = weeks[index]['platforms'].get(row['platform'], 0) + 1
        recent = [dict(r) for r in self.db.execute("""SELECT p.platform, p.at, p.source, c.name, c.fp FROM posts p
            LEFT JOIN clips c ON c.fp=p.fp ORDER BY p.at DESC LIMIT 40""")]
        watched = []
        for r in self.db.execute("""SELECT s.*, c.name FROM site_posts s LEFT JOIN clips c ON c.fp=s.fp
                WHERE s.counters IS NOT NULL ORDER BY s.created DESC, s.updated DESC LIMIT 100"""):
            watched.append(dict(platform=r['platform'], post_id=r['post_id'], created=r['created'], updated=r['updated'],
                                name=r['name'], fp=r['fp'], counters=json.loads(r['counters'] or '{}'),
                                url=library.post_url(r['platform'], r['post_id'], r['url'])))
        return dict(weeks=weeks, recent=recent, watched=watched)

    def picker(self, platform):
        """What Post Helper's picker shows on a site: planned posts, recent batches (attachable in one go), the library."""
        planned = [dict(r) for r in self.db.execute("""SELECT p.id, p.fp, p.at, p.caption, p.options, c.name FROM planned p
            JOIN clips c ON c.fp=p.fp WHERE p.platform=? AND p.done=0 ORDER BY p.at LIMIT 40""", (platform,))]
        for item in planned:
            item['options'] = json.loads(item['options'] or '{}')
            item['name'] = Path(item['name']).name
        batches = []
        for batch in self.db.execute('SELECT id, path FROM batches ORDER BY created DESC, id DESC LIMIT 5').fetchall():
            captions = {r['fp']: r['caption'] for r in self.db.execute(
                'SELECT fp, caption FROM planned WHERE batch=? AND platform=?', (batch['id'], platform))}
            # Every clip gets a caption (its name if none was planned) so bulk captions stay in step with the clips.
            items = [dict(fp=r['fp'], name=r['name'], caption=captions.get(r['fp']) or Path(r['name']).stem)
                     for r in self.db.execute('SELECT fp, name FROM batch_items WHERE batch=? ORDER BY rowid', (batch['id'],))]
            batches.append(dict(name=Path(batch['path']).name, items=items))
        posted = {r['fp']: r['n'] for r in self.db.execute('SELECT fp, COUNT(*) n FROM posts WHERE platform=? GROUP BY fp',
                                                           (platform,))}
        clips = [dict(fp=r['fp'], name=Path(r['name']).name) for r in self.db.execute(
            'SELECT fp, name FROM clips WHERE missing=0 ORDER BY mtime DESC LIMIT 200')]
        for item in planned + [i for b in batches for i in b['items']] + clips:
            item['posted'] = posted.get(item['fp'], 0)
        site_presets = library.presets(self.db, platform)
        return dict(platform=platform, planned=planned, batches=batches, library=clips,
                    options=library.preset_settings(self.db, platform) if site_presets else {},
                    presets=[dict(id=p['id'], name=p['name'], options={**p['settings'], 'preset': p['name']})
                             for p in site_presets])

    def file_path(self, fp):
        clip = self.db.execute('SELECT folder, name FROM clips WHERE fp=?', (fp,)).fetchone()
        candidates = [Path(clip['folder']) / clip['name']] if clip else []
        candidates += [Path(r['path']) / r['name'] for r in self.db.execute(
            'SELECT b.path, i.name FROM batch_items i JOIN batches b ON b.id=i.batch WHERE i.fp=?', (fp,))]
        return next((p for p in candidates if p.is_file()), None)

    def preview(self, body):
        folder = body['folder']
        clips = [dict(r) for r in self.db.execute(
            'SELECT * FROM clips WHERE folder=? AND missing=0 AND parent IS NULL ORDER BY mtime, name', (folder,))]
        rng = random.Random(body.get('seed'))
        chosen = library.choose(clips, library.last_used(self.db), int(body['count']), datetime.now(),
                                body.get('order', 'rules'), int(body.get('recent_days', 14)), int(body.get('window', 5)), rng)
        return clips, chosen

    def post(self, path, body, server):
        if path == '/api/folders':
            folder = body.get('path') or folder_dialog()
            if not folder:
                return dict(cancelled=True)
            if not Path(folder).is_dir():
                raise ValueError('That folder does not exist.')
            return dict(path=str(Path(folder).resolve()), count=library.sync(self.db, folder))
        if path == '/api/folders/remove':
            self.db.execute('DELETE FROM folders WHERE path=?', (body['path'],))
            self.db.commit()
            return dict(ok=True)
        if path == '/api/scan':
            return dict(count=library.sync(self.db, self.known(body['folder'])))
        if path == '/api/rename':
            folder = self.known(body['folder'])
            pattern = body['prefix']
            # "SFW" numbers files oldest first; a pattern with {N} numbers them 1, 2, 3... alphabetically.
            skip = library.batch_dirs(self.db, folder)
            plan, notes = library.template_plan(folder, pattern, skip, self.length) if library.TOKEN.search(pattern) \
                else (library.rename_plan(folder, pattern, skip), [])
            if body.get('apply'):
                library.apply_renames(self.db, folder, plan)
                self.db.execute('UPDATE folders SET prefix=? WHERE path=?', (pattern.strip(), folder))
                self.db.commit()
            return dict(plan=plan, notes=notes, applied=bool(body.get('apply')))
        if path == '/api/cleanup':
            folder = self.known(body['folder'])
            plan = library.cleanup_plan(folder, library.batch_dirs(self.db, folder), self.length)
            if body.get('apply'):
                library.apply_renames(self.db, folder, plan)
            return dict(plan=plan, applied=bool(body.get('apply')))
        if path == '/api/sort':
            folder = self.known(body['folder'])
            if 'autosort' in body:
                self.db.execute('UPDATE folders SET autosort=? WHERE path=?', (int(bool(body['autosort'])), folder))
                self.db.commit()
            plan = library.sort_plan(folder, library.batch_dirs(self.db, folder, moved=True))
            if body.get('apply'):
                library.apply_renames(self.db, folder, plan)
            return dict(plan=plan, applied=bool(body.get('apply')))
        if path == '/api/batch/preview':
            self.known(body['folder'])
            clips, chosen = self.preview(body)
            return dict(total=len(clips), chosen=[dict(fp=c['fp'], name=c['name'], reason=r) for c, r in chosen])
        if path == '/api/batch':
            folder = self.known(body['folder'])
            names = set(body['names'])
            clips = [dict(r) for r in self.db.execute('SELECT * FROM clips WHERE folder=? AND missing=0', (folder,))]
            chosen = [(c, '') for c in clips if c['name'] in names]
            name = (body.get('name') or '').strip() or None
            if name and (re.search(r'[<>:"/\\|?*]', name) or name.strip('.') == ''):
                raise ValueError('Use a folder name without < > : " / \\ | ? *')
            batch, target = library.make_batch(self.db, folder, chosen, datetime.now(), name, bool(body.get('move')))
            choice = body.get('presets') or {}  # site -> preset id; a site's first preset otherwise
            platforms = {p: choice.get(p) for p in body.get('platforms', []) if p in HELPER_SITES}
            items, skipped = library.helper_batch(self.db, batch, target, platforms, datetime.now(), self.probe) \
                if platforms else ([], [])
            return dict(path=str(target), name=target.name, copied=len(chosen), planned=len(items), skipped=skipped)
        if path == '/api/rotation/refresh':
            folder, platform, now = self.known(body['folder']), self.site(body['platform']), datetime.now()
            library.refresh_rotation(self.db, folder, platform, now)
            library.rotation_folder(self.db, platform, now, create=False)  # keep the month's folder in step
            return dict(ok=True)
        if path == '/api/rotation/folder':
            target = library.rotation_folder(self.db, self.site(body['platform']), datetime.now())
            if not target:
                raise ValueError('Nothing in this month’s pool yet.')
            os.startfile(target)
            return dict(path=str(target))
        if path == '/api/rotation/uploaded':
            library.mark_uploaded(self.db, body['ids'], datetime.now())
            return dict(ok=True)
        if path == '/api/rotation/undo':
            library.unmark_uploaded(self.db, int(body['id']))
            return dict(ok=True)
        if path == '/api/reveal':
            target = self.file_path(body['fp'])  # only files the library knows
            if not target:
                raise ValueError('That file is no longer on disk.')
            subprocess.Popen(['explorer', '/select,', str(target)])
            return dict(ok=True)
        if path == '/api/posted':
            self.helper_seen = datetime.now() if body.get('source') == 'post-helper' else self.helper_seen
            ok = library.record_post(self.db, body['platform'], name=body.get('name'), fp=body.get('fp'),
                                     at=body.get('at'), source=body.get('source', 'manual'))
            return dict(ok=ok)
        if path == '/api/presets':
            return library.save_preset(self.db, body['platform'], body.get('name', ''), body.get('values', {}), body.get('id'))
        if path == '/api/presets/delete':
            library.delete_preset(self.db, body['id'])
            return dict(ok=True)
        if path == '/api/tags':
            # Tag/category names a site suggested on its own upload page (seen by Post Helper).
            kind = body.get('kind') if body.get('kind') in ('tags', 'categories', 'niches') else 'tags'
            return dict(saved=library.learn_tags(self.db, body['platform'], kind, body.get('names', [])[:500]))
        if path == '/api/watch':
            # The creator's own posts as read by Post Helper from pages the site already loaded.
            self.helper_seen = datetime.now()
            return dict(saved=library.record_site_posts(self.db, body['platform'], body['posts'][:500]))
        if path == '/api/link':
            url = body.get('url')
            library.link_post(self.db, body['platform'], body['post_id'], body['fp'],
                              url if isinstance(url, str) and url.startswith('https://') else None)
            return dict(ok=True)
        if path == '/api/open':
            target = Path(body['path'])
            known = {r[0] for r in self.db.execute('SELECT path FROM folders UNION SELECT path FROM batches')}
            if str(target) not in known:
                raise ValueError('Only library and batch folders can be opened.')
            os.startfile(target)
            return dict(ok=True)
        if path == '/api/update':
            try:
                self.release = latest_release()  # the only time the app goes online, and only when asked
            except OSError:
                raise ValueError("Couldn't reach GitHub to check for updates. Everything else works offline.") from None
            if body.get('install') and self.release['available']:
                install_update(self.release, server)
                return dict(installing=True)
            return self.release
        if path == '/api/ffmpeg':
            if self.ffmpeg in ('missing', 'error'):
                self.ffmpeg = 'downloading'
                threading.Thread(target=self.get_ffmpeg, daemon=True).start()
            return dict(ffmpeg=self.ffmpeg)
        if path == '/api/show':
            # Opening the app again while it runs: bring this window to the front instead of starting another.
            if self.window:
                self.window.restore()
                self.window.show()
                self.window.on_top = True
                self.window.on_top = False
            return dict(ok=True)
        if path == '/api/quit':
            def stop():
                time.sleep(0.3)
                server.shutdown()
                if self.window:
                    self.window.destroy()  # closes the app window; the program then ends
                else:
                    os._exit(0)
            threading.Thread(target=stop, daemon=True).start()
            return dict(ok=True)
        return None

    def get_ffmpeg(self):
        from ffmpeg_tools import ensure_tools
        try:
            ensure_tools(tools_home())  # finds an installed FFmpeg, or downloads a pinned, checksum-verified build once
            self.ffmpeg = 'ready'
        except (OSError, ValueError, subprocess.SubprocessError):
            self.ffmpeg = 'error'

    def length(self, path):
        """Seconds of video, 0 if unknown (no FFmpeg): the backup check for teasers named like their clip."""
        return (self.probe(str(path)) or {}).get('duration') or 0

    def known(self, folder):
        if not self.db.execute('SELECT 1 FROM folders WHERE path=?', (folder,)).fetchone():
            raise ValueError('Add that folder to the library first.')
        return folder

    def site(self, platform):
        from scheduler import DEFAULTS
        if platform not in DEFAULTS:
            raise ValueError('Pick a site.')
        return platform

    def get(self, path, query):
        if path == '/api/rotation':
            now, folder = datetime.now(), self.known(query.get('folder', [''])[0])
            items = library.rotation(self.db, folder, self.site(query.get('platform', [''])[0]), now)
            names = [r['name'] for r in self.db.execute('SELECT name FROM clips WHERE folder=? AND missing=0', (folder,))]
            return dict(month=now.strftime('%B %Y'), items=items, files=len(names),
                        videos=sum(Path(n).suffix.lower() in library.VIDEO for n in names))
        if path == '/api/state':
            return self.state()
        if path == '/api/clips':
            return self.clip_rows(query.get('folder', [''])[0])
        if path.startswith('/api/clip/'):
            return self.detail(path.rsplit('/', 1)[1])
        if path == '/api/activity':
            return self.activity()
        if path == '/api/presets':
            from scheduler import DEFAULTS
            return dict(presets=library.presets(self.db), sites=list(DEFAULTS), helper=list(HELPER_SITES),
                        tags={site: library.known_tags(self.db, site) for site in DEFAULTS})
        if path == '/api/helper/clips':
            self.helper_seen = datetime.now()
            return self.picker(query.get('platform', [''])[0])
        return None


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            # Refuse other hostnames (DNS rebinding) and anything without our header (cross-site requests).
            host_ok = self.headers.get('Host', '') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
            return host_ok and self.headers.get(HEADER) == '1'

        def reply(self, status, body=b'', kind='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data, status=200):
            self.reply(status, json.dumps(data).encode())

        def do_GET(self):
            url = urlsplit(self.path)
            path = unquote(url.path)
            if self.headers.get('Host', '') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                return self.reply(403)
            if path in STATIC:
                name, kind = STATIC[path]
                return self.reply(200, (UI / name).read_bytes(), kind + '; charset=utf-8')
            if path.startswith('/thumb/') and path.endswith('.jpg'):
                fp = path[7:-4]
                with app.lock:
                    source = app.file_path(fp)
                thumb = source and library.thumbnail(fp, source, app.home / 'thumbs')
                return self.reply(200, thumb.read_bytes(), 'image/jpeg') if thumb else self.reply(404)
            if not self.allowed():
                return self.reply(403)
            if path.startswith('/file/'):
                with app.lock:
                    source = app.file_path(path[6:])
                if not source:
                    return self.reply(404)
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(source.stat().st_size))
                self.end_headers()
                with source.open('rb') as f:
                    while block := f.read(1 << 20):
                        self.wfile.write(block)
                return
            try:
                with app.lock:
                    data = app.get(path, parse_qs(url.query))
            except (ValueError, KeyError, OSError) as error:
                return self.send_json({'error': str(error)}, 400)
            return self.send_json(data) if data is not None else self.reply(404)

        def do_POST(self):
            if not self.allowed():
                return self.reply(403)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                path = urlsplit(self.path).path
                if path == '/api/folders' and not body.get('path'):
                    # Wait for the folder dialog without holding the app lock, so the dashboard keeps working.
                    body['path'] = folder_dialog()
                    if not body['path']:
                        return self.send_json({'cancelled': True})
                with app.lock:
                    data = app.post(urlsplit(self.path).path, body, self.server)
                return self.send_json(data) if data is not None else self.reply(404)
            except (ValueError, KeyError, OSError) as error:
                return self.send_json({'error': str(error)}, 400)

    return Handler


def keep_synced(app, every=300):
    """Rescan every library folder now and every few minutes, so auto-sort files new clips on its own."""
    def loop():
        while True:
            with app.lock:
                for row in app.db.execute('SELECT path FROM folders').fetchall():
                    try:
                        if Path(row['path']).is_dir():
                            library.sync(app.db, row['path'])
                    except OSError:
                        pass  # a file in use or a drive that went away: try again next time
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()


class Server(ThreadingHTTPServer):
    # Exclusive port: on Windows, address reuse would let a second copy share the port and answer some requests,
    # so a second launch must fail to bind and hand over to the running copy instead.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(app, port=PORT):
    return Server(('127.0.0.1', port), make_handler(app))


def show_running():
    """Ask the copy that is already running to bring its window forward; open the browser if it can't."""
    request = urllib.request.Request(f'http://127.0.0.1:{PORT}/api/show', data=b'{}', method='POST',
                                     headers={HEADER: '1', 'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except OSError:
        webbrowser.open(f'http://127.0.0.1:{PORT}/')


def open_window(app):
    """The dashboard in its own window (Windows' built-in WebView2), not a browser tab, so it never shows up
    in a streamed browser. Returns False if that isn't available here, and the browser is used instead."""
    try:
        import webview
    except ImportError:
        return False
    try:  # its own taskbar entry and icon, even when run from source rather than the packaged exe
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Ducky.ClipOrganizer')
    except (AttributeError, OSError):
        pass
    app.window = webview.create_window("Ducky’s Clip Organizer", f'http://127.0.0.1:{PORT}/', width=1320, height=860,
                                       min_size=(900, 600), background_color='#1c1a17')
    try:
        webview.start(private_mode=False, storage_path=str(library.DATA / 'window'),  # keeps view settings
                      icon=str(UI / 'icon.ico'))
    except Exception:  # no WebView2 on this PC
        app.window = None
        return False
    return True


def main():
    if '--pick-folder' in sys.argv:
        return pick_folder()
    exe = Path(sys.executable)
    if getattr(sys, 'frozen', False) and exe.with_suffix('.old').exists():
        try:
            exe.with_suffix('.old').unlink()  # left over from the last update
        except OSError:
            pass
    try:
        app = App(probe=probe_cached())
        server = serve(app)
    except OSError:
        return show_running()  # already running: show that window
    keep_synced(app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if open_window(app):
        server.shutdown()  # window closed: the app quits (Post Helper keeps posts until it's open again)
        return
    webbrowser.open(f'http://127.0.0.1:{PORT}/')
    threading.Event().wait()  # browser fallback: keep serving until the app is quit from the dashboard


def tools_home():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent


def ffmpeg_ready():
    from ffmpeg_tools import tool_path
    try:
        return all(tool_path(name, tools_home()) for name in ('ffmpeg', 'ffprobe'))
    except ValueError:
        return False


def probe_cached():
    from scheduler import probe_media
    cache = {}

    def probe(path):
        if path not in cache:
            if not ffmpeg_ready():
                # ponytail: without FFmpeg, length/shape checks are skipped rather than blocking every clip.
                return dict(duration=0, width=0, height=1)
            try:
                cache[path] = probe_media(path)
            except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
                cache[path] = None
        return cache[path]
    return probe


if __name__ == '__main__':
    main()
