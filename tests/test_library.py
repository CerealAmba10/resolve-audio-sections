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

    def test_scan_flags_missing_files(self):
        library.scan(self.db, self.folder)
        (self.folder / 'b.mp4').unlink()
        library.scan(self.db, self.folder)
        self.assertEqual([r['name'] for r in self.db.execute('SELECT name FROM clips WHERE missing=1')], ['b.mp4'])


if __name__ == '__main__':
    unittest.main()
