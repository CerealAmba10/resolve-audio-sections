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
        -- Named presets per site (tags, categories, captions, times...), chosen per batch or per post.
        CREATE TABLE IF NOT EXISTS presets(id INTEGER PRIMARY KEY, platform TEXT, name TEXT, settings TEXT,
            UNIQUE(platform, name));
        -- Tag and category names each site really offers, learned from its own suggestion lists.
        CREATE TABLE IF NOT EXISTS site_tags(platform TEXT, kind TEXT, name TEXT, uses INTEGER,
            PRIMARY KEY(platform, kind, name));
        CREATE TABLE IF NOT EXISTS site_post_days(platform TEXT, post_id TEXT, day TEXT, counters TEXT,
            PRIMARY KEY(platform, post_id, day));
        -- Manual uploads: a monthly pool of random clips per folder and site; post is set once it's uploaded.
        CREATE TABLE IF NOT EXISTS rotation(id INTEGER PRIMARY KEY, folder TEXT, platform TEXT, month TEXT, fp TEXT,
            post INTEGER);
    """)
    if 'url' not in {r['name'] for r in db.execute('PRAGMA table_info(site_posts)')}:
        db.execute('ALTER TABLE site_posts ADD COLUMN url TEXT')
    if 'autosort' not in {r['name'] for r in db.execute('PRAGMA table_info(folders)')}:
        db.execute('ALTER TABLE folders ADD COLUMN autosort INTEGER DEFAULT 0')
    if 'parent' not in {r['name'] for r in db.execute('PRAGMA table_info(clips)')}:
        db.execute('ALTER TABLE clips ADD COLUMN parent TEXT')  # a teaser's main clip
    if 'moved' not in {r['name'] for r in db.execute('PRAGMA table_info(batches)')}:
        db.execute('ALTER TABLE batches ADD COLUMN moved INTEGER DEFAULT 0')  # moved, not copied: still library files
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


def batch_dirs(db, folder, moved=False):
    """Folders of copies, left out of the library. Folders clips were moved into stay in it, but with
    moved=True they're listed too, so sorting leaves them where they were put."""
    return [r['path'] for r in db.execute('SELECT path FROM batches WHERE folder=? AND (moved=0 OR ?)',
                                          (str(folder), moved))]


DISCORD_ID = re.compile(r'[\s_-]*(?<!\d)(\d{17,20})(?!\d)')  # Discord attachment IDs; clip numbers are never this long
TEASER_WORD = re.compile(r'[\s_.(\[-]*\bteaser\b[\s_.)\]-]*', re.I)
TEASER_T = re.compile(r'(?<=\d)T\b|\bT(?=\d)|\bT\b')  # "12T", "T12" or "12 T"; not the T in 20261005T1200


def clean_stem(stem):
    """The name without Discord IDs: "IRL 1422922786464071841" -> "IRL"."""
    return re.sub(r'\s+', ' ', DISCORD_ID.sub(' ', stem)).strip(' _-') or stem


def key(stem):
    return re.sub(r'[\s_.-]+', ' ', clean_stem(stem)).strip().lower()


def teaser_key(stem):
    """For a teaser ("X teaser", "X 12T"), the key of the clip it belongs to; None for anything else."""
    stem = clean_stem(stem)
    for pattern in (TEASER_WORD, TEASER_T):
        if pattern.search(stem):
            return key(pattern.sub(' ', stem, count=1))
    return None


def snowflake(path):
    found = DISCORD_ID.findall(path.stem)
    return int(found[-1]) if found else None


TEASER_SECONDS = 50


def pairs(files, length=None):
    """{teaser path: clip path}: a teaser goes with the clip of the same name in the same folder. When several
    share a name, it goes with the one downloaded closest to it (nearest Discord ID, else modified time).
    With length (path -> seconds), a same-name video under 50 seconds is taken as the teaser of a longer one,
    and nothing 50 seconds or longer is ever a teaser, whatever its name says."""
    seconds = lambda p: (length(p) or 0) if length and p.suffix.lower() in VIDEO else 0
    named = lambda p: teaser_key(p.stem) if seconds(p) < TEASER_SECONDS else None
    clips = {}
    for path in files:
        if named(path) is None:
            clips.setdefault((path.parent, key(path.stem)), []).append(path)
    when = lambda p: snowflake(p) or p.stat().st_mtime
    found = {}
    same = {}  # by length, "Gym" and "Gym (2)" count as the same name: the (2) only marks a duplicate
    for (parent, k), group in clips.items() if length else ():
        same.setdefault((parent, re.sub(r' \(\d+\)$', '', k)), []).extend(group)
    for group in same.values():
        short = [p for p in group if 0 < seconds(p) < TEASER_SECONDS]
        full = [p for p in group if p not in short]
        for teaser in short if full else ():
            options = [c for c in full if c not in found.values()]
            if options:
                found[teaser] = min(options, key=lambda c: abs(when(c) - when(teaser)))
                clips[(teaser.parent, key(teaser.stem))].remove(teaser)
    for path in files:
        k = named(path)
        options = [c for c in clips.get((path.parent, k), []) if c not in found.values()] if k is not None else []
        if options:
            found[path] = min(options, key=lambda c: abs(when(c) - when(path)))
    return found


def cleanup_plan(folder, skip=(), length=None):
    """[(old, new)] taking Discord IDs out of downloaded names. Clips that end up with the same name are numbered
    "IRL", "IRL (2)"... and each teaser is named after its clip: "IRL (2) teaser"."""
    files = media_files(folder, skip)
    teasers = pairs(files, length)
    taken = {}  # (folder, lowercase name) -> path
    names = {}
    for path in sorted((p for p in files if p not in teasers), key=lambda p: (snowflake(p) is not None, snowflake(p) or 0)):
        stem, n = clean_stem(path.stem), 1
        while (path.parent, (stem + path.suffix).lower()) in taken:
            n += 1
            stem = f'{clean_stem(path.stem)} ({n})'
        taken[(path.parent, (stem + path.suffix).lower())] = path
        names[path] = stem
    for teaser, clip in teasers.items():  # keep a teaser name that already matches its clip ("12T", "X teaser")
        mine = clean_stem(teaser.stem)
        names[teaser] = mine if teaser_key(mine) == key(names[clip]) else f'{names[clip]} teaser'
    plan, finals = [], {}
    for path in files:
        new = rel(folder, path.with_name(names[path] + path.suffix.lower()))
        finals.setdefault(new.lower(), []).append(rel(folder, path))
        if new != rel(folder, path):
            plan.append((rel(folder, path), new))
    clashes = [names for names in finals.values() if len(names) > 1]
    if clashes:
        raise ValueError('These files would get the same name: ' + '; '.join(', '.join(n) for n in clashes[:5]))
    return plan


def scan(db, folder):
    """Add new files, follow renamed or moved ones and flag missing ones. Returns the number of clips found."""
    folder = str(Path(folder).resolve())
    now = datetime.now().strftime(FMT)
    db.execute('INSERT OR IGNORE INTO folders(path, added) VALUES(?, ?)', (folder, now))
    known = {(r['name'], r['size'], r['mtime']): r['fp'] for r in db.execute('SELECT * FROM clips WHERE folder=?', (folder,))}
    seen, fps = set(), {}
    files = media_files(folder, batch_dirs(db, folder))
    for path in files:
        stat = path.stat()
        name = rel(folder, path)
        fp = fps[path] = known.get((name, stat.st_size, stat.st_mtime)) or fingerprint(path)
        seen.add(fp)
        db.execute("""INSERT INTO clips(fp, folder, name, size, mtime, added) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(fp) DO UPDATE SET folder=excluded.folder, name=excluded.name, size=excluded.size,
            mtime=excluded.mtime, missing=0""", (fp, folder, name, stat.st_size, stat.st_mtime, now))
    db.execute('UPDATE clips SET parent=NULL WHERE folder=?', (folder,))
    db.executemany('UPDATE clips SET parent=? WHERE fp=?', [(fps[clip], fps[teaser]) for teaser, clip in pairs(files).items()])
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


TOKEN = re.compile(r'\{(N(?::(\d+))?|DATE|NAME|T)\}')
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


def natural(path):
    """Sort key matching File Explorer: "clip 2" before "clip 10", case ignored."""
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', str(path).lower())]


def template_plan(folder, template, skip=(), length=None):
    """Bulk rename from a pattern such as "SFW {N} {DATE}".
    {N}: 1, 2, 3... in alphabetical order of the current names (folder by folder); {N:3} pads to 3 digits.
    {DATE}: the date in the file's name as MM-DD-YY (the file's modified date if the name has none).
    {NAME}: the current name.
    {T}: "T" for a teaser (paired with its clip, or any video under 50 seconds), nothing for other clips; a teaser
    paired with clip 12 takes number 12. Without {T}, paired teasers get "12T" in place of {N}.
    Returns ([(old, new)], notes)."""
    template = template.strip()
    if re.search(r'[<>:"/\\|?*]', TOKEN.sub('', template)):
        raise ValueError('Use a name without < > : " / \\ | ? *')
    if not re.search(r'\{N(:\d+)?\}|\{NAME\}', template):
        raise ValueError('Add {N} or {NAME} so every file ends up with a different name.')
    files = sorted(media_files(folder, skip), key=lambda p: natural(rel(folder, p.with_suffix(''))))
    teasers = pairs(files, length)
    assigned = {path: n for n, path in enumerate((p for p in files if p not in teasers), 1)}
    marked = '{T}' in template
    seconds = lambda p: (length(p) or 0) if length and p.suffix.lower() in VIDEO else 0
    plan, finals, notes = [], {}, []
    for path in files:
        source = teasers.get(path, path)  # a teaser takes its clip's date, so the pair keeps matching
        date = name_date(source.stem)[0] or datetime.fromtimestamp(source.stat().st_mtime)

        def fill(match):
            if match.group(1) == 'DATE':
                return date.strftime('%m-%d-%y')
            if match.group(1) == 'NAME':
                return path.stem
            if match.group(1) == 'T':
                short = 0 < seconds(path) < TEASER_SECONDS and path not in teasers.values()  # a clip with a teaser isn't one
                return 'T' if path in teasers or short else ''
            if path in teasers:  # the teaser of clip 12 is "12T"
                return str(assigned[teasers[path]]).zfill(int(match.group(2) or 0)) + ('' if marked else 'T')
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
    files = media_files(folder, skip)
    teasers = pairs(files)
    for path in files:
        source = teasers.get(path, path)  # a teaser goes where its clip goes
        date = name_date(source.stem)[0] or datetime.fromtimestamp(source.stat().st_mtime)
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
            plan = sort_plan(folder, batch_dirs(db, folder, moved=True))
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


ORDERS = ('rules', 'name', 'name-desc', 'oldest', 'newest', 'random')


def choose(clips, used, count, now, order='rules', recent_days=14, window=5, rng=random):
    """[(clip, reason)]: the batch rules (see pick), or simply the first `count` in name, date or random order."""
    if order == 'rules':
        return pick(clips, used, count, now, recent_days, window, rng)
    if order not in ORDERS:
        raise ValueError('Unknown order.')
    clips = list(clips)  # oldest first
    if order in ('name', 'name-desc'):
        clips.sort(key=lambda c: natural(Path(c['name']).with_suffix('')), reverse=order == 'name-desc')
    elif order == 'newest':
        clips.reverse()
    elif order == 'random':
        rng.shuffle(clips)
    return [(c, f"last used {(now - used[c['fp']]).days} days ago" if c['fp'] in used else 'never posted')
            for c in clips[:count]]


def make_batch(db, folder, chosen, now, name=None, move=False):
    """Copy (or move) the chosen clips, each with its teaser, into a subfolder of the library folder and remember
    the batch. Copies are left out of the library; moved clips stay in it, history and all."""
    target = Path(folder) / (name or batch_name(folder, now))
    target.mkdir(exist_ok=move)  # moving into an existing folder ("oct") just adds to it
    created = now.strftime(FMT)
    batch = db.execute('INSERT INTO batches(folder, path, created, moved) VALUES(?, ?, ?, ?)',
                       (str(folder), str(target), created, int(move))).lastrowid
    transfer = shutil.move if move else shutil.copy2

    def place(source):
        name = source.name
        n = 1
        while (target / name).exists() and (target / name) != source:  # same file name from two subfolders
            n += 1
            name = f'{source.stem} ({n}){source.suffix}'
        if target / name != source:
            transfer(source, target / name)
        return name
    for clip, _ in chosen:
        name = place(Path(clip['folder']) / clip['name'])
        db.execute('INSERT INTO batch_items(batch, fp, name) VALUES(?, ?, ?)', (batch, clip['fp'], name))
        for teaser in db.execute('SELECT folder, name FROM clips WHERE parent=? AND missing=0', (clip['fp'],)).fetchall():
            if (Path(teaser['folder']) / teaser['name']).is_file():
                place(Path(teaser['folder']) / teaser['name'])
    db.commit()
    if move:
        scan(db, folder)  # the library follows the files to their new folder
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


VIDEO = {'.mp4', '.mov', '.mkv', '.webm', '.m4v', '.avi'}


def fill_rotation(db, folder, platform, month, size, rng=random):
    """Add `size` random videos to a month's pool: ones never posted on this site first, nothing already in it."""
    taken = {r['fp'] for r in db.execute('SELECT fp FROM rotation WHERE folder=? AND platform=? AND month=?',
                                         (folder, platform, month))}
    posted = {r['fp'] for r in db.execute('SELECT fp FROM posts WHERE platform=?', (platform,))}
    clips = [r['fp'] for r in db.execute('SELECT fp, name FROM clips WHERE folder=? AND missing=0 AND parent IS NULL '
                                         'ORDER BY name', (folder,))
             if Path(r['name']).suffix.lower() in VIDEO and r['fp'] not in taken]
    fresh, rest = [fp for fp in clips if fp not in posted], [fp for fp in clips if fp in posted]
    rng.shuffle(fresh)
    rng.shuffle(rest)
    db.executemany('INSERT INTO rotation(folder, platform, month, fp) VALUES(?, ?, ?, ?)',
                   [(folder, platform, month, fp) for fp in (fresh + rest)[:size]])
    db.commit()


def rotation(db, folder, platform, now, size=30, rng=random):
    """This month's pool for a site, picked the first time it's opened in a new month."""
    month = now.strftime('%Y-%m')
    args = (folder, platform, month)
    if not db.execute('SELECT 1 FROM rotation WHERE folder=? AND platform=? AND month=?', args).fetchone():
        fill_rotation(db, folder, platform, month, size, rng)
    return [dict(r) for r in db.execute("""SELECT r.id, r.fp, c.name, c.missing, p.at uploaded FROM rotation r
        JOIN clips c ON c.fp=r.fp LEFT JOIN posts p ON p.id=r.post
        WHERE r.folder=? AND r.platform=? AND r.month=? ORDER BY r.id""", args)]


def refresh_rotation(db, folder, platform, now, size=30, rng=random):
    """Drop everything not uploaded yet from this month's pool and pick `size` new videos."""
    month = now.strftime('%Y-%m')
    db.execute('DELETE FROM rotation WHERE folder=? AND platform=? AND month=? AND post IS NULL', (folder, platform, month))
    fill_rotation(db, folder, platform, month, size, rng)


def mark_uploaded(db, ids, now):
    """Log each queued pool item as posted on its site today; the post carries the site and the month."""
    ids = [int(i) for i in ids]
    if not ids:
        return
    for row in db.execute(f"SELECT * FROM rotation WHERE post IS NULL AND id IN ({','.join('?' * len(ids))})", ids).fetchall():
        post = db.execute('INSERT INTO posts(fp, platform, at, source) VALUES(?, ?, ?, ?)',
                          (row['fp'], row['platform'], now.strftime(FMT), 'manual upload')).lastrowid
        db.execute('UPDATE rotation SET post=? WHERE id=?', (post, row['id']))
    db.commit()


def unmark_uploaded(db, item):
    row = db.execute('SELECT post FROM rotation WHERE id=?', (item,)).fetchone()
    if row and row['post']:
        db.execute('DELETE FROM posts WHERE id=?', (row['post'],))
        db.execute('UPDATE rotation SET post=NULL WHERE id=?', (item,))
        db.commit()


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


def check_settings(site, values):
    """A site's settings with these changes, checked: types match, times and caption template can be used."""
    from scheduler import DEFAULTS, next_slots
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
    merged = {**DEFAULTS[site], **clean, 'enabled': True}
    if merged['clip'] not in ('teaser', 'full', 'any'):
        raise ValueError('Clip type must be teaser, full or any.')
    next(next_slots(merged['times'], datetime.now(), set()))  # raises if the posting times can't be read
    try:
        merged['caption'].format(name='', tags='')
    except (KeyError, IndexError):
        raise ValueError('The caption can only use {name} and {tags}.') from None
    return merged


def ensure_presets(db):
    """Every site starts with a "Default" preset, taken from the planner's settings."""
    from scheduler import DEFAULTS
    have = {r['platform'] for r in db.execute('SELECT DISTINCT platform FROM presets')}
    missing = [site for site in DEFAULTS if site not in have]
    for site, settings in platform_settings(missing).items():
        db.execute('INSERT INTO presets(platform, name, settings) VALUES(?, ?, ?)', (site, 'Default', json.dumps(settings)))
    db.commit()


def presets(db, platform=None):
    rows = db.execute('SELECT * FROM presets WHERE ? IS NULL OR platform=? ORDER BY platform, id', (platform, platform))
    return [dict(id=r['id'], platform=r['platform'], name=r['name'], settings=json.loads(r['settings'])) for r in rows]


def preset_settings(db, platform, preset_id=None):
    """The chosen preset's settings for a site (its first preset if none or a stale one is given)."""
    found = presets(db, platform)
    chosen = next((p for p in found if p['id'] == preset_id), found[0] if found else None)
    if not chosen:
        raise ValueError(f'No preset for {platform}.')
    return {**chosen['settings'], 'enabled': True, 'preset': chosen['name']}


def save_preset(db, platform, name, values, preset_id=None):
    name = str(name).strip()
    if not name:
        raise ValueError('Give the preset a name.')
    settings = check_settings(platform, values)
    try:
        if preset_id:
            db.execute('UPDATE presets SET name=?, settings=? WHERE id=? AND platform=?',
                       (name, json.dumps(settings), preset_id, platform))
        else:
            preset_id = db.execute('INSERT INTO presets(platform, name, settings) VALUES(?, ?, ?)',
                                   (platform, name, json.dumps(settings))).lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f'{platform} already has a preset called "{name}".') from None
    db.commit()
    return next(p for p in presets(db, platform) if p['id'] == preset_id)


def delete_preset(db, preset_id):
    row = db.execute('SELECT platform FROM presets WHERE id=?', (preset_id,)).fetchone()
    if not row:
        return
    if db.execute('SELECT COUNT(*) FROM presets WHERE platform=?', (row['platform'],)).fetchone()[0] <= 1:
        raise ValueError('Each site keeps at least one preset.')
    db.execute('DELETE FROM presets WHERE id=?', (preset_id,))
    db.commit()


def learn_tags(db, platform, kind, names):
    """Remember tag or category names the site itself suggested, so presets can be checked against them."""
    rows = [(platform, kind, str(n).strip()[:80]) for n in names if str(n).strip()][:2000]
    db.executemany("""INSERT INTO site_tags(platform, kind, name, uses) VALUES(?, ?, ?, 1)
        ON CONFLICT(platform, kind, name) DO UPDATE SET uses=uses+1""", rows)
    db.commit()
    return len(rows)


def known_tags(db, platform):
    out = {}
    for r in db.execute('SELECT kind, name FROM site_tags WHERE platform=? ORDER BY uses DESC, name', (platform,)):
        out.setdefault(r['kind'], []).append(r['name'])
    return out


def helper_batch(db, batch, target, platforms, now, probe):
    """batch.json for Post Helper. platforms: {site: preset id or None}; each site uses that preset's settings."""
    from scheduler import plan
    settings = {site: preset_settings(db, site, preset_id) for site, preset_id in platforms.items()}
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
