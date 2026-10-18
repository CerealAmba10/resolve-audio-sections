"""Clip library: folders, renaming, posting history and batch picking. Clips are tracked by a content
fingerprint, so renaming or copying a file never loses its history."""
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

DATA = Path(os.environ.get('CLIP_ORGANIZER_HOME') or
            Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Clip Organizer')  # not synced to OneDrive
MEDIA = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi', '.jpg', '.jpeg', '.png', '.gif', '.webp'}
FMT = '%Y-%m-%d %H:%M'
SAMPLE = 1 << 20


def connect(home=DATA):
    Path(home).mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(Path(home) / 'library.db', timeout=30, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS folders(path TEXT PRIMARY KEY, prefix TEXT DEFAULT '', added TEXT);
        CREATE TABLE IF NOT EXISTS clips(fp TEXT PRIMARY KEY, folder TEXT, name TEXT, size INTEGER, mtime REAL,
            added TEXT, missing INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, fp TEXT, platform TEXT, at TEXT, source TEXT);
        CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, folder TEXT, path TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS batch_items(batch INTEGER, fp TEXT, name TEXT);
        CREATE TABLE IF NOT EXISTS planned(id INTEGER PRIMARY KEY, fp TEXT, platform TEXT, at TEXT, batch INTEGER,
            caption TEXT, options TEXT, done INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS posts_fp ON posts(fp);
    """)
    return db


def fingerprint(path):
    """Size plus the first and last megabyte: fast on big videos, stable across renames and copies."""
    path = Path(path)
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open('rb') as f:
        digest.update(f.read(SAMPLE))
        if size > SAMPLE:
            f.seek(max(SAMPLE, size - SAMPLE))
            digest.update(f.read(SAMPLE))
    return digest.hexdigest()[:32]


def media_files(folder):
    return sorted((p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in MEDIA),
                  key=lambda p: (p.stat().st_mtime, p.name))


def scan(db, folder):
    """Add new files, follow renamed ones and flag missing ones. Returns the number of clips found."""
    folder = str(Path(folder).resolve())
    now = datetime.now().strftime(FMT)
    db.execute('INSERT OR IGNORE INTO folders(path, added) VALUES(?, ?)', (folder, now))
    known = {(r['name'], r['size'], r['mtime']): r['fp'] for r in db.execute('SELECT * FROM clips WHERE folder=?', (folder,))}
    seen = set()
    for path in media_files(folder):
        stat = path.stat()
        fp = known.get((path.name, stat.st_size, stat.st_mtime)) or fingerprint(path)
        seen.add(fp)
        db.execute("""INSERT INTO clips(fp, folder, name, size, mtime, added) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(fp) DO UPDATE SET folder=excluded.folder, name=excluded.name, size=excluded.size,
            mtime=excluded.mtime, missing=0""", (fp, folder, path.name, stat.st_size, stat.st_mtime, now))
    gone = [r['fp'] for r in db.execute('SELECT fp FROM clips WHERE folder=?', (folder,)) if r['fp'] not in seen]
    db.executemany('UPDATE clips SET missing=1 WHERE fp=?', [(fp,) for fp in gone])
    db.commit()
    return len(seen)


def rename_plan(folder, prefix):
    """[(old name, new name)] numbering files 'Prefix 1', 'Prefix 2'... oldest first. Files already
    numbered with this prefix keep their number; new files continue after the highest one."""
    prefix = prefix.strip()
    if not prefix or re.search(r'[<>:"/\\|?*]', prefix):
        raise ValueError('Use a prefix without < > : " / \\ | ? *')
    pattern = re.compile(re.escape(prefix) + r' (\d+)$', re.I)
    files = media_files(folder)
    kept = {p.name for p in files if pattern.match(p.stem)}
    used = {int(pattern.match(Path(n).stem).group(1)) for n in kept}
    number = max(used, default=0)
    plan = []
    for path in files:
        if path.name in kept:
            continue
        number += 1
        plan.append((path.name, f'{prefix} {number}{path.suffix.lower()}'))
    return plan


def apply_renames(db, folder, plan):
    """Two-step rename so swaps and clashes can never overwrite a file."""
    folder = Path(folder)
    targets = {new.lower() for _, new in plan}
    sources = {old.lower() for old, _ in plan}
    for p in folder.iterdir():
        if p.name.lower() in targets and p.name.lower() not in sources:
            raise ValueError(f'{p.name} already exists in this folder.')
    temps = []
    for i, (old, new) in enumerate(plan):
        temp = folder / f'.renaming-{os.getpid()}-{i}{Path(old).suffix}'
        (folder / old).rename(temp)
        temps.append((temp, new))
    for temp, new in temps:
        temp.rename(folder / new)
    return scan(db, folder)


def last_used(db):
    """When each clip was last posted or put in a batch."""
    used = {}
    for row in db.execute("""SELECT fp, MAX(at) at FROM (SELECT fp, at FROM posts UNION ALL
            SELECT i.fp, b.created FROM batch_items i JOIN batches b ON b.id=i.batch) GROUP BY fp"""):
        used[row['fp']] = datetime.strptime(row['at'], FMT)
    return used


def pick(clips, used, count, now, recent_days=14, window=5, rng=random):
    """Choose clips for a batch:
    1. never-posted clips first, oldest first;
    2. then clips whose last use is oldest, picked at random from the `window` oldest for variety;
    3. never anything used in the last `recent_days` days.
    clips: [{'fp', 'name', ...}] oldest first. Returns [(clip, reason)]."""
    cutoff = now - timedelta(days=recent_days)
    fresh = [c for c in clips if c['fp'] not in used]
    rested = sorted((c for c in clips if c['fp'] in used and used[c['fp']] <= cutoff), key=lambda c: used[c['fp']])
    chosen = [(c, 'never posted') for c in fresh[:count]]
    while len(chosen) < count and rested:
        c = rested.pop(rng.randrange(min(max(window, 1), len(rested))))
        chosen.append((c, f"last used {(now - used[c['fp']]).days} days ago"))
    return chosen


def batch_name(root, now):
    base = now.strftime('%B %Y')
    name, n = base, 1
    while (Path(root) / name).exists():
        n += 1
        name = f'{base} ({n})'
    return name


def make_batch(db, folder, chosen, now, name=None):
    """Copy the chosen clips into a new subfolder of the library folder and remember the batch."""
    target = Path(folder) / (name or batch_name(folder, now))
    target.mkdir()
    created = now.strftime(FMT)
    batch = db.execute('INSERT INTO batches(folder, path, created) VALUES(?, ?, ?)',
                       (str(folder), str(target), created)).lastrowid
    for clip, _ in chosen:
        shutil.copy2(Path(clip['folder']) / clip['name'], target / clip['name'])
        db.execute('INSERT INTO batch_items(batch, fp, name) VALUES(?, ?, ?)', (batch, clip['fp'], clip['name']))
    db.commit()
    return batch, target


def record_post(db, platform, name=None, fp=None, at=None, source='manual'):
    """Log a post. Post Helper reports file names; a name maps to the clip in the newest batch holding it."""
    if not fp:
        row = db.execute("""SELECT i.fp FROM batch_items i JOIN batches b ON b.id=i.batch WHERE i.name=?
            ORDER BY b.created DESC LIMIT 1""", (name,)).fetchone() or \
            db.execute('SELECT fp FROM clips WHERE name=? ORDER BY added DESC LIMIT 1', (name,)).fetchone()
        if not row:
            return False
        fp = row['fp']
    db.execute('INSERT INTO posts(fp, platform, at, source) VALUES(?, ?, ?, ?)',
               (fp, platform, at or datetime.now().strftime(FMT), source))
    # The earliest open plan for this clip on this site is now done.
    db.execute("""UPDATE planned SET done=1 WHERE id=(SELECT id FROM planned WHERE fp=? AND platform=? AND done=0
        ORDER BY at LIMIT 1)""", (fp, platform))
    db.commit()
    return True


def thumbnail(fp, source, cache):
    """A small JPEG for the library view, made once with FFmpeg."""
    out = Path(cache) / f'{fp}.jpg'
    if not out.exists():
        from ffmpeg_tools import tool_path
        out.parent.mkdir(parents=True, exist_ok=True)
        image = Path(source).suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
        seek = [] if image else ['-ss', '1']
        subprocess.run([str(tool_path('ffmpeg', Path(__file__).parent)), '-v', 'error', '-y', *seek, '-i', str(source),
                        '-frames:v', '1', '-vf', 'scale=320:-2', '-q:v', '5', str(out)],
                       capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return out if out.exists() else None


def helper_batch(db, batch, target, platforms, now, probe):
    """batch.json for Post Helper, using the planner's per-site settings and daily posting times."""
    from scheduler import DEFAULTS, HOME, plan
    try:
        saved = json.loads((HOME / 'settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    settings = {name: {**DEFAULTS[name], **{k: v for k, v in saved.get(name, {}).items() if k in DEFAULTS[name]},
                       'enabled': True} for name in platforms}
    taken = [dict(platform=r['platform'], at=r['at'], file='') for r in
             db.execute('SELECT platform, at FROM planned WHERE at >= ?', (now.strftime(FMT),))]
    files = [str(target / r['name']) for r in db.execute('SELECT name FROM batch_items WHERE batch=?', (batch,))]
    posts, skipped = plan(files, settings, taken, now, probe)
    fps = {r['name']: r['fp'] for r in db.execute('SELECT name, fp FROM batch_items WHERE batch=?', (batch,))}
    db.executemany('INSERT INTO planned(fp, platform, at, batch, caption, options) VALUES(?, ?, ?, ?, ?, ?)',
                   [(fps[Path(p['file']).name], p['platform'], p['at'], batch, p['caption'], p['options']) for p in posts])
    db.commit()
    items = [dict(platform=p['platform'], file=Path(p['file']).name, at=p['at'], caption=p['caption'],
                  options=json.loads(p['options'])) for p in posts]
    (target / 'batch.json').write_text(json.dumps(items, indent=2), encoding='utf-8')
    return items, skipped
