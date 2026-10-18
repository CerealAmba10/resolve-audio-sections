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
            Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Duckys Clip Organizer')  # not synced to OneDrive
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
        -- Site posts seen by Post Helper's watcher: latest numbers, plus one reading per day for trends.
        CREATE TABLE IF NOT EXISTS site_posts(platform TEXT, post_id TEXT, fp TEXT, created TEXT, counters TEXT,
            updated TEXT, PRIMARY KEY(platform, post_id));
        CREATE TABLE IF NOT EXISTS site_post_days(platform TEXT, post_id TEXT, day TEXT, counters TEXT,
            PRIMARY KEY(platform, post_id, day));
    """)
    if 'url' not in {r['name'] for r in db.execute('PRAGMA table_info(site_posts)')}:
        db.execute('ALTER TABLE site_posts ADD COLUMN url TEXT')
    if 'autosort' not in {r['name'] for r in db.execute('PRAGMA table_info(folders)')}:
        db.execute('ALTER TABLE folders ADD COLUMN autosort INTEGER DEFAULT 0')
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


def media_files(folder, skip=()):
    """Media in the folder and its subfolders, oldest first. Batch folders (copies) in `skip` are left out."""
    folder = Path(folder).resolve()
    skip = [Path(s).resolve() for s in skip]
    files = [p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in MEDIA and not p.name.startswith('.renaming-')
             and not any(s == p.parent or s in p.parents for s in skip)]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name))


def rel(folder, path):
    """A clip's name in the library: its path inside the managed folder, e.g. "2025/SFW 12.mp4"."""
    return Path(path).resolve().relative_to(Path(folder).resolve()).as_posix()


def batch_dirs(db, folder):
    return [r['path'] for r in db.execute('SELECT path FROM batches WHERE folder=?', (str(folder),))]


def scan(db, folder):
    """Add new files, follow renamed or moved ones and flag missing ones. Returns the number of clips found."""
    folder = str(Path(folder).resolve())
    now = datetime.now().strftime(FMT)
    db.execute('INSERT OR IGNORE INTO folders(path, added) VALUES(?, ?)', (folder, now))
    known = {(r['name'], r['size'], r['mtime']): r['fp'] for r in db.execute('SELECT * FROM clips WHERE folder=?', (folder,))}
    seen = set()
    for path in media_files(folder, batch_dirs(db, folder)):
        stat = path.stat()
        name = rel(folder, path)
        fp = known.get((name, stat.st_size, stat.st_mtime)) or fingerprint(path)
        seen.add(fp)
        db.execute("""INSERT INTO clips(fp, folder, name, size, mtime, added) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(fp) DO UPDATE SET folder=excluded.folder, name=excluded.name, size=excluded.size,
            mtime=excluded.mtime, missing=0""", (fp, folder, name, stat.st_size, stat.st_mtime, now))
    gone = [r['fp'] for r in db.execute('SELECT fp FROM clips WHERE folder=?', (folder,)) if r['fp'] not in seen]
    db.executemany('UPDATE clips SET missing=1 WHERE fp=?', [(fp,) for fp in gone])
    db.commit()
    return len(seen)


def rename_plan(folder, prefix, skip=()):
    """[(old name, new name)] numbering files 'Prefix 1', 'Prefix 2'... oldest first. Files already
    numbered with this prefix keep their number; new files continue after the highest one."""
    prefix = prefix.strip()
    if not prefix or re.search(r'[<>:"/\\|?*]', prefix):
        raise ValueError('Use a prefix without < > : " / \\ | ? *')
    pattern = re.compile(re.escape(prefix) + r' (\d+)$', re.I)
    files = media_files(folder, skip)
    kept = {p for p in files if pattern.match(p.stem)}
    number = max((int(pattern.match(p.stem).group(1)) for p in kept), default=0)
    plan = []
    for path in files:
        if path in kept:
            continue
        number += 1
        plan.append((rel(folder, path), rel(folder, path.with_name(f'{prefix} {number}{path.suffix.lower()}'))))
    return plan


TOKEN = re.compile(r'\{(N(?::(\d+))?|DATE|NAME)\}')
DATES = [(re.compile(r'(?<!\d)((?:19|20)\d{2})[-_. ]?(\d{2})[-_. ]?(\d{2})(?!\d)'), 'ymd'),
         (re.compile(r'(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](\d{4}|\d{2})(?!\d)'), 'mdy')]


def name_date(stem):
    """(date, span) of the first date written in a file name, or (None, None).
    Reads 2026-10-05, 20261005, 10-05-26 and 10.05.2026; a first part over 12 is taken as the day."""
    for pattern, order in DATES:
        for match in pattern.finditer(stem):
            a, b, c = (int(g) for g in match.groups())
            if order == 'ymd':
                year, month, day = a, b, c
            else:
                month, day, year = (b, a, c) if a > 12 >= b else (a, b, c)
                year += 2000 if year < 100 else 0
            try:
                return datetime(year, month, day), match.span()
            except ValueError:
                continue
    return None, None


def name_number(stem):
    """A clip's sequence number: the last number in its name once any date is taken out."""
    _, span = name_date(stem)
    if span:
        stem = stem[:span[0]] + ' ' + stem[span[1]:]
    numbers = re.findall(r'\d+', stem)
    return int(numbers[-1]) if numbers else None


def template_plan(folder, template, skip=()):
    """Bulk rename from a pattern such as "SFW {N} {DATE}".
    {N}: the number already in the file's name (a free number if it has none or it's taken); {N:3} pads to 3 digits.
    {DATE}: the date in the file's name as MM-DD-YY (the file's modified date if the name has none).
    {NAME}: the current name. Returns ([(old, new)], notes)."""
    template = template.strip()
    if re.search(r'[<>:"/\\|?*]', TOKEN.sub('', template)):
        raise ValueError('Use a name without < > : " / \\ | ? *')
    if not re.search(r'\{N(:\d+)?\}|\{NAME\}', template):
        raise ValueError('Add {N} or {NAME} so every file ends up with a different name.')
    files = media_files(folder, skip)
    numbers = {p: name_number(p.stem) for p in files}
    highest = max((n for n in numbers.values() if n is not None), default=0)
    assigned, taken, notes = {}, set(), []
    for path in files:  # oldest first, so the older file keeps a shared number
        n = numbers[path]
        if n is None or n in taken:
            highest += 1
            if n is not None:
                notes.append(f'{path.name}: number {n} is already used, so it becomes {highest}.')
            n = highest
        taken.add(n)
        assigned[path] = n
    plan, finals = [], {}
    for path in files:
        date = name_date(path.stem)[0] or datetime.fromtimestamp(path.stat().st_mtime)

        def fill(match):
            if match.group(1) == 'DATE':
                return date.strftime('%m-%d-%y')
            if match.group(1) == 'NAME':
                return path.stem
            return str(assigned[path]).zfill(int(match.group(2) or 0))
        new = rel(folder, path.with_name(re.sub(r'\s+', ' ', TOKEN.sub(fill, template)).strip() + path.suffix.lower()))
        finals.setdefault(new.lower(), []).append(rel(folder, path))
        if new != rel(folder, path):
            plan.append((rel(folder, path), new))
    clashes = [names for names in finals.values() if len(names) > 1]
    if clashes:
        raise ValueError('These files would get the same name: ' + '; '.join(', '.join(n) for n in clashes[:5]))
    return plan, notes


def sort_plan(folder, skip=()):
    """Move clips into a folder per year with a subfolder per month: "2025/10 October/clip.mp4".
    The date comes from the file name (2025-10-05, 10-05-25...), else the date the file was saved."""
    plan, finals = [], {}
    for path in media_files(folder, skip):
        date = name_date(path.stem)[0] or datetime.fromtimestamp(path.stat().st_mtime)
        old, new = rel(folder, path), f"{date.year}/{date.strftime('%m %B')}/{path.name}"
        finals.setdefault(new.lower(), []).append(old)
        if old.lower() != new.lower():
            plan.append((old, new))
    clashes = [names for names in finals.values() if len(names) > 1]
    if clashes:
        raise ValueError('These files have the same name and would land in the same folder: ' +
                         '; '.join(', '.join(n) for n in clashes[:5]) + '. Rename them first.')
    return plan


def apply_renames(db, folder, plan):
    """Rename or move in two steps so swaps and clashes can never overwrite a file; then tidy empty folders."""
    folder = Path(folder)
    sources = {old.lower() for old, _ in plan}
    for _, new in plan:
        if (folder / new).exists() and new.lower() not in sources:
            raise ValueError(f'{new} already exists.')
    temps = []
    for i, (old, new) in enumerate(plan):
        temp = folder / f'.renaming-{os.getpid()}-{i}{Path(old).suffix}'
        (folder / old).rename(temp)
        temps.append((temp, new))
    for temp, new in temps:
        (folder / new).parent.mkdir(parents=True, exist_ok=True)
        temp.rename(folder / new)
    for parent in sorted({(folder / old).parent for old, _ in plan}, key=lambda p: -len(p.parts)):
        while parent != folder and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()  # a folder emptied by the move
            parent = parent.parent
    return scan(db, folder)


def sync(db, folder):
    """Scan a folder and, if auto-sort is on for it, file new clips into their year/month folders."""
    count = scan(db, folder)
    row = db.execute('SELECT autosort FROM folders WHERE path=?', (str(Path(folder).resolve()),)).fetchone()
    if row and row['autosort']:
        try:
            plan = sort_plan(folder, batch_dirs(db, folder))
        except ValueError:
            return count  # name clash: leave it for the person to sort out in Organize
        if plan:
            count = apply_renames(db, folder, plan)
    return count


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
        name = Path(clip['name']).name
        stem, n = Path(name).stem, 1
        while (target / name).exists():  # same file name from two subfolders
            n += 1
            name = f'{stem} ({n}){Path(clip["name"]).suffix}'
        shutil.copy2(Path(clip['folder']) / clip['name'], target / name)
        db.execute('INSERT INTO batch_items(batch, fp, name) VALUES(?, ?, ?)', (batch, clip['fp'], name))
    db.commit()
    return batch, target


def record_post(db, platform, name=None, fp=None, at=None, source='manual'):
    """Log a post. Post Helper reports file names; a name maps to the clip in the newest batch holding it."""
    if not fp:
        row = db.execute("""SELECT i.fp FROM batch_items i JOIN batches b ON b.id=i.batch WHERE i.name=?
            ORDER BY b.created DESC LIMIT 1""", (name,)).fetchone() or \
            db.execute("SELECT fp FROM clips WHERE name=? OR name LIKE ? ORDER BY added DESC LIMIT 1",
                       (name, '%/' + name)).fetchone()
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


POST_URLS = {'Fansly': 'https://fansly.com/post/{id}'}  # add RedGifs/Pornhub once their post pages are known


def post_url(platform, post_id, saved=None):
    template = POST_URLS.get(platform)
    return saved or (template.format(id=post_id) if template else None)


def link_post(db, platform, post_id, fp, url=None):
    """Tie a site post (and its address, when the site gave one) to the clip Post Helper attached to it."""
    db.execute("""INSERT INTO site_posts(platform, post_id, fp, url) VALUES(?, ?, ?, ?)
        ON CONFLICT(platform, post_id) DO UPDATE SET fp=excluded.fp, url=COALESCE(excluded.url, url)""",
               (platform, str(post_id), fp, url))
    db.commit()


def record_site_posts(db, platform, posts, now=None):
    """Save the numbers the watcher read for the creator's own posts: [{id, created, counters}].
    Only the latest numbers and one reading per day are kept, so storage stays tiny."""
    now = now or datetime.now()
    stamp, day = now.strftime(FMT), now.strftime('%Y-%m-%d')
    for post in posts:
        counters = json.dumps({k: v for k, v in post.get('counters', {}).items() if isinstance(v, (int, float))})
        db.execute("""INSERT INTO site_posts(platform, post_id, created, counters, updated) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(platform, post_id) DO UPDATE SET created=COALESCE(excluded.created, created),
            counters=excluded.counters, updated=excluded.updated""",
                   (platform, str(post['id']), post.get('created'), counters, stamp))
        db.execute('INSERT OR REPLACE INTO site_post_days(platform, post_id, day, counters) VALUES(?, ?, ?, ?)',
                   (platform, str(post['id']), day, counters))
    db.commit()
    return len(posts)


def clip_totals(db, fp):
    """A clip's numbers added up across every site post it became."""
    totals, posts = {}, []
    for row in db.execute('SELECT * FROM site_posts WHERE fp=? ORDER BY created DESC', (fp,)):
        counters = json.loads(row['counters'] or '{}')
        posts.append(dict(platform=row['platform'], post_id=row['post_id'], created=row['created'],
                          counters=counters, updated=row['updated'], url=post_url(row['platform'], row['post_id'], row['url'])))
        for k, v in counters.items():
            totals[k] = totals.get(k, 0) + v
    return totals, posts


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


def platform_settings(platforms):
    """The planner's per-site settings (defaults plus anything changed in its Settings window)."""
    from scheduler import DEFAULTS, HOME
    try:
        saved = json.loads((HOME / 'settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    return {name: {**DEFAULTS[name], **{k: v for k, v in saved.get(name, {}).items() if k in DEFAULTS[name]},
                   'enabled': True} for name in platforms if name in DEFAULTS}


def save_platform_settings(site, values):
    """Check and save one site's settings (shared with the planner's settings.json)."""
    from scheduler import DEFAULTS, HOME, next_slots
    if site not in DEFAULTS:
        raise ValueError('Unknown site.')
    clean = {}
    for key, value in values.items():
        if key not in DEFAULTS[site] or key == 'enabled':
            continue
        kind = type(DEFAULTS[site][key])
        try:
            clean[key] = bool(value) if kind is bool else kind(value)
        except (TypeError, ValueError):
            raise ValueError(f'{key.replace("_", " ")} must be a {"number" if kind in (int, float) else "text"}.') from None
    merged = {**DEFAULTS[site], **clean}
    if merged['clip'] not in ('teaser', 'full', 'any'):
        raise ValueError('Clip type must be teaser, full or any.')
    next(next_slots(merged['times'], datetime.now(), set()))  # raises if the posting times can't be read
    try:
        merged['caption'].format(name='', tags='')
    except (KeyError, IndexError):
        raise ValueError('The caption can only use {name} and {tags}.') from None
    path = HOME / 'settings.json'
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    saved[site] = {**saved.get(site, {}), **clean}
    HOME.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.json.tmp')
    temp.write_text(json.dumps(saved, indent=2), encoding='utf-8')
    os.replace(temp, path)
    return platform_settings([site])[site]


def helper_batch(db, batch, target, platforms, now, probe):
    """batch.json for Post Helper, using the planner's per-site settings and daily posting times."""
    from scheduler import plan
    settings = platform_settings(platforms)
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
