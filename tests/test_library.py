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

    def test_name_dates(self):
        cases = {'clip 2026-10-05 take 7': datetime(2026, 10, 5), 'IMG_20261005_0012': datetime(2026, 10, 5),
                 'stream 10-05-26 part3': datetime(2026, 10, 5), '25.12.2025 night 4': datetime(2025, 12, 25),
                 'SFW 12': None, 'IRL 1422922786464071841': None}
        for stem, date in cases.items():
            self.assertEqual(library.name_date(stem)[0], date, stem)

    def test_pattern_rename_numbers_alphabetically(self):
        for name in list(self.folder.iterdir()):
            name.unlink()
        names = ['take 7 2026-10-05.mp4', 'IRL 1422922786464071841.mp4', 'clip 10.mp4', 'Clip 2.mp4', 'loose.mp4']
        for i, name in enumerate(names):
            (self.folder / name).write_bytes(name.encode())
            os.utime(self.folder / name, (1000 + i, 1000 + i))
        plan, _ = library.template_plan(self.folder, 'SFW {N:3} {DATE}')
        stamp = lambda i: datetime.fromtimestamp(1000 + i).strftime('%m-%d-%y')  # no date in the name: modified date
        self.assertEqual(dict(plan), {'Clip 2.mp4': f'SFW 001 {stamp(3)}.mp4',  # "clip 2" before "clip 10", case ignored
                                      'clip 10.mp4': f'SFW 002 {stamp(2)}.mp4',
                                      'IRL 1422922786464071841.mp4': f'SFW 003 {stamp(1)}.mp4',
                                      'loose.mp4': f'SFW 004 {stamp(4)}.mp4',
                                      'take 7 2026-10-05.mp4': 'SFW 005 10-05-26.mp4'})
        library.apply_renames(self.db, self.folder, plan)
        self.assertEqual(library.template_plan(self.folder, 'SFW {N:3} {DATE}')[0], [])  # running it again changes nothing
        self.assertEqual(sorted(p.name for p in self.folder.iterdir())[0], 'SFW 001 ' + stamp(3) + '.mp4')
        with self.assertRaises(ValueError):
            library.template_plan(self.folder, 'Only {DATE}')  # every name would clash

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

    def fill(self, names):
        for name in list(self.folder.iterdir()):
            name.unlink()
        for i, name in enumerate(names):
            (self.folder / name).write_bytes(name.encode())
            os.utime(self.folder / name, (1000 + i, 1000 + i))

    def test_cleanup_downloads_and_teasers(self):
        self.fill(['IRL 1422922786464071841.mp4', 'IRL teaser 1422922786464071842.mp4', 'IRL 1422922786464079999.mp4',
                   'IRL Teaser_1422922786464080000.mp4', 'Beach 12.mp4', 'Beach 12T.mp4', 'IMG_20261005T1200.mp4'])
        plan = library.cleanup_plan(self.folder)
        self.assertEqual(dict(plan), {'IRL 1422922786464071841.mp4': 'IRL.mp4',
                                      'IRL teaser 1422922786464071842.mp4': 'IRL teaser.mp4',  # nearest ID = same post
                                      'IRL 1422922786464079999.mp4': 'IRL (2).mp4',
                                      'IRL Teaser_1422922786464080000.mp4': 'IRL (2) teaser.mp4'})
        library.apply_renames(self.db, self.folder, plan)
        parent = {r['name']: r['parent'] for r in self.db.execute('SELECT name, parent FROM clips')}
        fp = {r['name']: r['fp'] for r in self.db.execute('SELECT name, fp FROM clips')}
        self.assertEqual({n: p for n, p in parent.items() if p}, {'IRL teaser.mp4': fp['IRL.mp4'],
                         'IRL (2) teaser.mp4': fp['IRL (2).mp4'], 'Beach 12T.mp4': fp['Beach 12.mp4']})
        self.assertIsNone(parent['IMG_20261005T1200.mp4'])  # a timestamp's T is not a teaser
        plan, _ = library.template_plan(self.folder, 'SFW {N}')
        self.assertEqual(dict(plan), {'Beach 12.mp4': 'SFW 1.mp4', 'Beach 12T.mp4': 'SFW 1T.mp4',
                                      'IMG_20261005T1200.mp4': 'SFW 2.mp4', 'IRL.mp4': 'SFW 3.mp4',
                                      'IRL teaser.mp4': 'SFW 3T.mp4', 'IRL (2).mp4': 'SFW 4.mp4',
                                      'IRL (2) teaser.mp4': 'SFW 4T.mp4'})  # teasers take their clip's number + T
        library.apply_renames(self.db, self.folder, plan)
        self.assertEqual(self.db.execute("SELECT parent FROM clips WHERE name='SFW 4T.mp4'").fetchone()[0],
                         self.db.execute("SELECT fp FROM clips WHERE name='SFW 4.mp4'").fetchone()[0])

    def test_short_same_name_video_is_the_teaser(self):
        self.fill(['Gym 1422922786464071841.mp4', 'Gym 1422922786464071900.mp4', 'Solo 1422922786464075000.mp4'])
        seconds = {'Gym 1422922786464071841': 95, 'Gym 1422922786464071900': 20, 'Solo 1422922786464075000': 12}
        plan = library.cleanup_plan(self.folder, length=lambda p: seconds[p.stem])
        self.assertEqual(dict(plan), {'Gym 1422922786464071841.mp4': 'Gym.mp4', 'Gym 1422922786464071900.mp4': 'Gym teaser.mp4',
                                      'Solo 1422922786464075000.mp4': 'Solo.mp4'})  # short but alone: a clip
        self.assertEqual(dict(library.cleanup_plan(self.folder))['Gym 1422922786464071900.mp4'], 'Gym (2).mp4')  # no lengths

    def test_t_token_marks_teasers_by_pair_or_length(self):
        self.fill(['Gym.mp4', 'Gym (2).mp4', 'Run.mp4', 'Run teaser.mp4', 'Short.mp4', 'Long teaser.mp4'])
        seconds = {'Gym': 95, 'Gym (2)': 20, 'Run': 80, 'Run teaser': 15, 'Short': 9, 'Long teaser': 120}
        plan, _ = library.template_plan(self.folder, 'SFW {N}{T}', length=lambda p: seconds[p.stem])
        self.assertEqual(dict(plan), {'Gym.mp4': 'SFW 1.mp4', 'Gym (2).mp4': 'SFW 1T.mp4',  # same name, 20s: its teaser
                                      'Long teaser.mp4': 'SFW 2.mp4',  # 2 minutes: never a teaser
                                      'Run.mp4': 'SFW 3.mp4', 'Run teaser.mp4': 'SFW 3T.mp4',
                                      'Short.mp4': 'SFW 4T.mp4'})  # under 50 seconds, on its own
        library.apply_renames(self.db, self.folder, plan)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM clips WHERE parent IS NOT NULL").fetchone()[0], 2)  # 4T has no clip
        plan, _ = library.template_plan(self.folder, 'SFW {N} {T}', length=lambda p: 30)  # short clips with teasers: no T
        self.assertEqual(plan[0], ('SFW 1T.mp4', 'SFW 1 T.mp4'))
        library.apply_renames(self.db, self.folder, plan)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM clips WHERE parent IS NOT NULL").fetchone()[0], 2)  # "1 T" pairs

    def test_move_into_named_folder_takes_teasers(self):
        self.fill(['a.mp4', 'a teaser.mp4', 'b.mp4', 'c.mp4'])
        library.scan(self.db, self.folder)
        clips = [dict(r) for r in self.db.execute('SELECT * FROM clips WHERE parent IS NULL ORDER BY mtime')]
        used = {clips[0]['fp']: NOW - timedelta(days=1)}
        self.assertEqual([c['name'] for c, _ in library.choose(clips, used, 2, NOW, 'name-desc')], ['c.mp4', 'b.mp4'])
        self.assertEqual([c['name'] for c, _ in library.choose(clips, used, 3, NOW, 'oldest')], ['a.mp4', 'b.mp4', 'c.mp4'])
        self.assertEqual(library.choose(clips, used, 1, NOW, 'oldest')[0][1], 'last used 1 days ago')
        library.make_batch(self.db, self.folder, library.choose(clips, used, 1, NOW, 'name'), NOW, 'oct', move=True)
        self.assertEqual(sorted(p.name for p in (self.folder / 'oct').iterdir()), ['a teaser.mp4', 'a.mp4'])
        self.assertEqual(sorted(r['name'] for r in self.db.execute('SELECT name FROM clips WHERE missing=0')),
                         ['b.mp4', 'c.mp4', 'oct/a teaser.mp4', 'oct/a.mp4'])  # still in the library
        library.make_batch(self.db, self.folder, [(c, '') for c in clips[1:2]], NOW, 'oct', move=True)  # adds to "oct"
        self.assertTrue((self.folder / 'oct' / 'b.mp4').exists())
        self.db.execute('UPDATE folders SET autosort=1')
        library.sync(self.db, self.folder)
        self.assertTrue((self.folder / 'oct' / 'a.mp4').exists())  # sorting leaves the moved folder alone

    def test_monthly_manual_rotation(self):
        for n in range(40):
            (self.folder / f'v{n}.mp4').write_bytes(f'video {n}'.encode())
        library.scan(self.db, self.folder)
        folder = str(self.folder.resolve())
        library.record_post(self.db, 'TikTok', name='v0.mp4')  # posted elsewhere: still fresh for Instagram
        pool = library.rotation(self.db, folder, 'Instagram', NOW)
        self.assertEqual(len(pool), 30)
        self.assertTrue(all(p['name'].lower().endswith(('.mp4', '.mov')) for p in pool))  # videos only, no images
        self.assertEqual(library.rotation(self.db, folder, 'Instagram', NOW), pool)  # same pool all month
        library.mark_uploaded(self.db, [pool[0]['id'], pool[1]['id']], NOW)
        library.mark_uploaded(self.db, [pool[0]['id']], NOW)  # twice: logged once
        posts = self.db.execute("SELECT * FROM posts WHERE platform='Instagram'").fetchall()
        self.assertEqual([(p['fp'], p['at'][:7]) for p in posts], [(pool[0]['fp'], '2026-10'), (pool[1]['fp'], '2026-10')])
        library.unmark_uploaded(self.db, pool[1]['id'])
        library.refresh_rotation(self.db, folder, 'Instagram', NOW)
        fresh = library.rotation(self.db, folder, 'Instagram', NOW)
        self.assertEqual(fresh[0]['fp'], pool[0]['fp'])  # the uploaded one stays, greyed out
        self.assertEqual((len(fresh), sum(1 for p in fresh if p['uploaded'])), (31, 1))
        self.assertEqual(len({p['fp'] for p in fresh}), 31)
        self.assertNotEqual({p['fp'] for p in library.rotation(self.db, folder, 'Instagram', NOW + timedelta(days=31))},
                            set())  # next month: a new pool
        self.assertEqual(len(library.rotation(self.db, folder, 'TikTok', NOW)), 30)  # each site has its own
        self.db.execute("UPDATE rotation SET fp='gone' WHERE platform='Reddit'")
        library.rotation(self.db, folder, 'Reddit', NOW)
        self.db.execute("UPDATE rotation SET fp='gone' WHERE platform='Reddit'")  # clips no longer in the library
        self.assertEqual(len(library.rotation(self.db, folder, 'Reddit', NOW)), 30)  # a blank pool is picked again
        (self.folder / 'still.png').write_bytes(b'image')
        library.scan(self.db, self.folder)
        library.refresh_rotation(self.db, folder, 'Reddit', NOW, size=100, force=True)  # force takes any file
        self.assertIn('still.png', [p['name'] for p in library.rotation(self.db, folder, 'Reddit', NOW)])
        other = Path(self.temp.name, 'other')
        other.mkdir()
        (other / 'o.mp4').write_bytes(b'other folder')
        library.scan(self.db, other)
        self.assertEqual(library.rotation(self.db, str(other.resolve()), 'TikTok', NOW)[0]['fp'],
                         library.rotation(self.db, folder, 'TikTok', NOW)[0]['fp'])  # same pool from any folder
        library.refresh_rotation(self.db, str(other.resolve()), 'TikTok', NOW)  # a new rotation picks from the open one
        self.assertEqual([Path(p['name']).name for p in library.rotation(self.db, folder, 'TikTok', NOW)], ['o.mp4'])

    def test_rotation_folder_holds_the_months_pool(self):
        self.fill(['Gym.mp4', 'Gym teaser.mp4', 'IRL.mp4'])
        (self.folder / 'sub').mkdir()
        (self.folder / 'sub' / 'IRL.mp4').write_bytes(b'other irl')
        library.scan(self.db, self.folder)
        folder = str(self.folder.resolve())
        self.assertEqual(len(library.rotation(self.db, folder, 'Instagram', NOW)), 3)  # teasers aren't picked
        target = library.rotation_folder(self.db, 'Instagram', NOW)
        self.assertEqual(target, self.folder / 'Manual uploads' / 'Instagram October 2026')
        self.assertEqual({p.name for p in target.iterdir()},  # the teaser comes along; same names get (2)
                         {'Gym.mp4', 'Gym teaser.mp4', 'IRL.mp4', 'IRL (2).mp4'})
        (target / 'my notes.txt').write_text('mine')
        self.assertEqual(library.scan(self.db, self.folder), 4)  # the copies aren't new clips
        library.refresh_rotation(self.db, folder, 'Instagram', NOW, size=0)  # pool emptied
        self.assertIsNone(library.rotation_folder(self.db, 'Instagram', NOW))
        library.rotation(self.db, folder, 'Instagram', NOW, size=1)
        self.assertEqual(library.rotation_folder(self.db, 'Instagram', NOW), target)
        library.refresh_rotation(self.db, folder, 'Instagram', NOW, size=0)
        self.db.execute("INSERT INTO rotation(folder, platform, month, fp) VALUES(?, 'Instagram', '2026-10', 'none')", (folder,))
        library.rotation_folder(self.db, 'Instagram', NOW)
        self.assertEqual([p.name for p in target.iterdir()], ['my notes.txt'])  # copies gone, the rest left alone
        self.assertEqual(len(list(self.folder.rglob('*.mp4'))), 4)  # originals untouched

    def test_scan_flags_missing_files(self):
        library.scan(self.db, self.folder)
        (self.folder / 'b.mp4').unlink()
        library.scan(self.db, self.folder)
        self.assertEqual([r['name'] for r in self.db.execute('SELECT name FROM clips WHERE missing=1')], ['b.mp4'])


if __name__ == '__main__':
    unittest.main()
