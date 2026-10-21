import os
import random
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import library

NOW = datetime(2026, 10, 17, 12, 0)


def clip(n):
    return {'fp': f'fp{n}', 'name': f'SFW {n}.mp4', 'folder': ''}


class PickTests(unittest.TestCase):
    def test_rules(self):
        clips = [clip(n) for n in range(1, 11)]
        used = {'fp1': NOW - timedelta(days=90), 'fp2': NOW - timedelta(days=60), 'fp3': NOW - timedelta(days=30),
                'fp4': NOW - timedelta(days=3), 'fp5': NOW - timedelta(days=120)}
        chosen = library.pick(clips, used, 8, NOW, recent_days=14, window=1)
        names = [c['fp'] for c, _ in chosen]
        self.assertEqual(names[:5], ['fp6', 'fp7', 'fp8', 'fp9', 'fp10'])  # 1. never posted first, oldest first
        self.assertEqual(names[5:], ['fp5', 'fp1', 'fp2'])  # 2. then longest-rested first
        self.assertNotIn('fp4', names)  # 4. nothing used in the last 14 days
        self.assertEqual(chosen[0][1], 'never posted')
        self.assertEqual(chosen[5][1], 'last used 120 days ago')

    def test_variance_stays_near_the_oldest_and_quantity_caps(self):
        clips = [clip(n) for n in range(1, 21)]
        used = {c['fp']: NOW - timedelta(days=200 - i) for i, c in enumerate(clips)}
        firsts = {library.pick(clips, used, 1, NOW, window=5, rng=random.Random(seed))[0][0]['fp'] for seed in range(50)}
        self.assertEqual(firsts, {'fp1', 'fp2', 'fp3', 'fp4', 'fp5'})  # 3. variety, but only among the oldest
        self.assertEqual(len(library.pick(clips, used, 7, NOW)), 7)  # 5. quantity
        self.assertEqual(len(library.pick(clips, {k: NOW for k in used}, 7, NOW)), 0)  # all recent: nothing


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name, 'clips')
        self.folder.mkdir()
        self.db = library.connect(Path(self.temp.name, 'data'))
        for i, name in enumerate(['b.mp4', 'SFW 2.mp4', 'a.MOV', 'notes.txt']):
            (self.folder / name).write_bytes(name.encode() * 100)
            os.utime(self.folder / name, (1000 + i, 1000 + i))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_rename_keeps_numbers_and_history(self):
        self.assertEqual(library.scan(self.db, self.folder), 3)
        library.record_post(self.db, 'Fansly', name='b.mp4', at='2026-10-01 12:00')
        plan = library.rename_plan(self.folder, 'SFW')
        self.assertEqual(plan, [('b.mp4', 'SFW 3.mp4'), ('a.MOV', 'SFW 4.mov')])  # oldest first, after SFW 2
        library.apply_renames(self.db, self.folder, plan)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['SFW 2.mp4', 'SFW 3.mp4', 'SFW 4.mov', 'notes.txt'])
        row = self.db.execute("SELECT COUNT(*) n FROM posts p JOIN clips c ON c.fp=p.fp WHERE c.name='SFW 3.mp4'").fetchone()
        self.assertEqual(row['n'], 1)  # history follows the file through the rename
        with self.assertRaises(ValueError):
            library.rename_plan(self.folder, 'bad/name')

    def test_batch_folder_copies_and_reserves(self):
        library.scan(self.db, self.folder)
        clips = [dict(r) for r in self.db.execute('SELECT * FROM clips')]
        chosen = library.pick(clips, library.last_used(self.db), 2, NOW)
        batch, target = library.make_batch(self.db, self.folder, chosen, NOW)
        self.assertEqual(target.name, 'October 2026')  # 6. month + year subfolder
        self.assertEqual(len(list(target.iterdir())), 2)
        self.assertEqual(library.make_batch(self.db, self.folder, chosen[:1], NOW)[1].name, 'October 2026 (2)')
        later = library.pick(clips, library.last_used(self.db), 3, NOW + timedelta(days=1))
        self.assertEqual(len(later), 1)  # batched clips count as recently used
        self.assertTrue(library.record_post(self.db, 'RedGifs', name=chosen[0][0]['name']))
        self.assertFalse(library.record_post(self.db, 'RedGifs', name='unknown.mp4'))

    def test_name_dates_and_numbers(self):
        cases = {'clip 2026-10-05 take 7': (datetime(2026, 10, 5), 7), 'IMG_20261005_0012': (datetime(2026, 10, 5), 12),
                 'stream 10-05-26 part3': (datetime(2026, 10, 5), 3), '25.12.2025 night 4': (datetime(2025, 12, 25), 4),
                 'SFW 12': (None, 12), 'no numbers': (None, None),
                 'IRL 1422922786464071841': (None, None),  # a download ID, not a clip number
                 'take 3 1422922786464071841': (None, 3)}
        for stem, (date, number) in cases.items():
            self.assertEqual((library.name_date(stem)[0], library.name_number(stem)), (date, number), stem)

    def test_pattern_rename_moves_number_and_date(self):
        for name in list(self.folder.iterdir()):
            name.unlink()
        names = ['take 7 2026-10-05.mp4', 'IMG_20261003_0012.mp4', 'loose.mp4', 'dup 7.mp4']
        for i, name in enumerate(names):
            (self.folder / name).write_bytes(name.encode())
            os.utime(self.folder / name, (1000 + i, 1000 + i))
        plan, notes = library.template_plan(self.folder, 'SFW {N:3} {DATE}')
        stamp = datetime.fromtimestamp(1002).strftime('%m-%d-%y')  # no date in the name: modified date
        self.assertEqual(dict(plan), {'take 7 2026-10-05.mp4': 'SFW 007 10-05-26.mp4',
                                      'IMG_20261003_0012.mp4': 'SFW 012 10-03-26.mp4',
                                      'loose.mp4': f'SFW 013 {stamp}.mp4',
                                      'dup 7.mp4': f"SFW 014 {datetime.fromtimestamp(1003).strftime('%m-%d-%y')}.mp4"})
        self.assertEqual(notes, ['dup 7.mp4: number 7 is already used, so it becomes 14.'])
        library.apply_renames(self.db, self.folder, plan)
        self.assertEqual(library.template_plan(self.folder, 'SFW {N:3} {DATE}')[0], [])  # running it again changes nothing
        with self.assertRaises(ValueError):
            library.template_plan(self.folder, 'Only {DATE}')  # every name would clash

    def test_pattern_rename_ignores_download_ids(self):
        for name in list(self.folder.iterdir()):
            name.unlink()
        for i, name in enumerate(['IRL 1422922786464071841.mp4', 'IRL 5.mp4', 'clip_1422922786464071999.mp4']):
            (self.folder / name).write_bytes(name.encode())
            os.utime(self.folder / name, (1000 + i, 1000 + i))
        plan, _ = library.template_plan(self.folder, 'IRL {N}')
        self.assertEqual(dict(plan), {'IRL 1422922786464071841.mp4': 'IRL 6.mp4',  # next free, not the ID
                                      'clip_1422922786464071999.mp4': 'IRL 7.mp4'})

    def test_sort_into_year_and_month_folders(self):
        for name in list(self.folder.iterdir()):
            name.unlink()
        old = self.folder / 'loose'
        old.mkdir()
        (old / 'take 2025-03-14.mp4').write_bytes(b'a')
        (self.folder / 'IMG_20261003_7.mp4').write_bytes(b'b')
        undated = self.folder / 'undated.mp4'
        undated.write_bytes(b'c')
        os.utime(undated, (datetime(2024, 12, 31, 12).timestamp(),) * 2)
        library.scan(self.db, self.folder)
        library.record_post(self.db, 'Fansly', name='undated.mp4', at='2026-01-01 12:00')
        batch, target = library.make_batch(self.db, self.folder, [(dict(self.db.execute(
            "SELECT * FROM clips WHERE name='IMG_20261003_7.mp4'").fetchone()), '')], NOW)
        plan = library.sort_plan(self.folder, library.batch_dirs(self.db, self.folder))
        self.assertEqual(sorted(plan), [('IMG_20261003_7.mp4', '2026/10 October/IMG_20261003_7.mp4'),
                                        ('loose/take 2025-03-14.mp4', '2025/03 March/take 2025-03-14.mp4'),
                                        ('undated.mp4', '2024/12 December/undated.mp4')])  # batch copy left alone
        self.assertEqual(library.apply_renames(self.db, self.folder, plan), 3)
        self.assertFalse(old.exists())  # emptied folder tidied away
        self.assertTrue((target / 'IMG_20261003_7.mp4').exists())
        row = self.db.execute("SELECT COUNT(*) n FROM posts p JOIN clips c ON c.fp=p.fp "
                              "WHERE c.name='2024/12 December/undated.mp4'").fetchone()
        self.assertEqual(row['n'], 1)  # history follows the move
        self.assertEqual(library.sort_plan(self.folder, library.batch_dirs(self.db, self.folder)), [])

    def test_autosort_only_when_switched_on(self):
        for name in list(self.folder.iterdir()):
            name.unlink()
        (self.folder / 'new 2026-02-01.mp4').write_bytes(b'x')
        library.sync(self.db, self.folder)
        self.assertTrue((self.folder / 'new 2026-02-01.mp4').exists())  # off by default
        self.db.execute('UPDATE folders SET autosort=1')
        library.sync(self.db, self.folder)
        self.assertTrue((self.folder / '2026' / '02 February' / 'new 2026-02-01.mp4').exists())

    def test_site_post_numbers_add_up_per_clip(self):
        library.link_post(self.db, 'Fansly', '900', 'clipA')  # linked when posted, numbers arrive later
        library.record_site_posts(self.db, 'Fansly', [{'id': '900', 'created': '2026-10-01 12:00',
                                                       'counters': {'likeCount': 5, 'tipAmount': 200, 'note': 'x'}}], NOW)
        library.record_site_posts(self.db, 'Fansly', [{'id': '901', 'counters': {'likeCount': 3}}], NOW)
        library.link_post(self.db, 'Fansly', '901', 'clipA')  # or numbers first, link after
        library.record_site_posts(self.db, 'Fansly', [{'id': '900', 'counters': {'likeCount': 7, 'tipAmount': 200}}],
                                  NOW + timedelta(hours=2))  # same day: the reading is replaced, not added
        totals, posts = library.clip_totals(self.db, 'clipA')
        self.assertEqual(totals, {'likeCount': 10, 'tipAmount': 200})
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]['created'], '2026-10-01 12:00')  # kept when a later reading lacks it
        self.assertEqual(posts[0]['url'], 'https://fansly.com/post/900')  # each post's link is kept with its clip
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM site_post_days').fetchone()[0], 2)

    def test_scan_flags_missing_files(self):
        library.scan(self.db, self.folder)
        (self.folder / 'b.mp4').unlink()
        library.scan(self.db, self.folder)
        self.assertEqual([r['name'] for r in self.db.execute('SELECT name FROM clips WHERE missing=1')], ['b.mp4'])


if __name__ == '__main__':
    unittest.main()
