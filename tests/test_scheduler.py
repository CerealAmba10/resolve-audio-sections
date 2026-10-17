import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scheduler import DEFAULTS, clip_kind, connect, next_slots, plan, problems, save_posts, stage


class SchedulerTests(unittest.TestCase):
    def test_kind_and_limits(self):
        self.assertEqual(clip_kind('show 3T.mp4'), 'teaser')
        self.assertEqual(clip_kind('show 3.mp4'), 'full')
        info = dict(duration=30, width=1920, height=1080)
        self.assertEqual(problems(DEFAULTS['Fansly'], 'a 1T.mp4', info, 0), ['not a full clip'])
        self.assertEqual(problems(DEFAULTS['TikTok'], 'a 1T.mp4', info, 0), ['1920x1080 is not vertical'])
        self.assertEqual(problems(DEFAULTS['RedGifs'], 'a 1T.mp4', dict(info, duration=90), 0), ['90s > 60s'])

    def test_slots_skip_past_and_taken(self):
        slots = next_slots('20:00, 12:00', datetime(2026, 1, 1, 13, 0), {'2026-01-01 20:00'})
        self.assertEqual([next(slots) for _ in range(2)],
                         [datetime(2026, 1, 2, 12), datetime(2026, 1, 2, 20)])

    def test_plan_freezes_options_and_exports_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            teaser, full = Path(folder, 'rec 1T.mp4'), Path(folder, 'rec 1.mp4')
            teaser.write_bytes(b'x')
            full.write_bytes(b'x')
            settings = {k: dict(v, enabled=k in ('Fansly', 'RedGifs')) for k, v in DEFAULTS.items()}
            info = lambda p: dict(duration=30, width=1080, height=1920)
            new, skipped = plan([str(teaser), str(full)], settings, [], datetime(2026, 1, 1, 9), info)
            self.assertEqual({(p['platform'], Path(p['file']).name) for p in new},
                             {('Fansly', 'rec 1.mp4'), ('RedGifs', 'rec 1T.mp4')})
            self.assertEqual(len(skipped), 2)
            self.assertEqual(json.loads(new[0]['options'])['audience'], 'subscribers')
            again, _ = plan([str(teaser), str(full)], settings, new, datetime(2026, 1, 1, 9), info)
            self.assertEqual(again, [])  # already scheduled per platform
            folders = stage(new, Path(folder, 'out'))
            self.assertTrue((folders['Fansly'] / 'rec 1.mp4').exists())
            self.assertIn('rec 1T', (folders['RedGifs'] / 'captions.txt').read_text(encoding='utf-8'))
            batch = json.loads((folders['Fansly'] / 'batch.json').read_text(encoding='utf-8'))
            self.assertEqual(batch, [dict(platform='Fansly', file='rec 1.mp4', at='2026-01-01 12:00', caption='rec 1',
                                          options=json.loads(new[0]['options']))])

    def test_queue_survives_reopen_and_refuses_duplicates(self):
        with tempfile.TemporaryDirectory() as home:
            post = dict(platform='Fansly', file='c.mp4', at='2026-01-02 12:00', caption='', options='{}')
            db = connect(home)
            save_posts(db, [post])
            db.close()
            db = connect(home)
            self.assertEqual([r['status'] for r in db.execute('SELECT status FROM posts')], ['planned'])
            with self.assertRaises(sqlite3.IntegrityError):
                save_posts(db, [post])
            db.close()


if __name__ == '__main__':
    unittest.main()
