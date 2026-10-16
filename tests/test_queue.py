import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from lupa.lua51 import LuaRuntime, LuaError

from audio_sections import prepare_queue


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "film ' é.mp4"
        self.source.write_bytes(b'test source')
        self.folder = prepare_queue(self.source, self.root, {'fps': Fraction(24)},
                                    [(5, 45), (55, 95)], 1, 1, 'Audio Sections High')
        self.code = (self.folder / 'queue.lua').read_text(encoding='utf-8')
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(Path('tests/resolve_fake.lua').read_text(encoding='utf-8'))

    def tearDown(self):
        self.temp.cleanup()

    def test_queue_has_isolated_pairs_native_fades_and_high_quality(self):
        self.lua.execute(self.code)
        state = self.lua.globals().state
        jobs = list(state.jobs.values())
        self.assertEqual(len(jobs), 5)
        self.assertEqual([j.settings.CustomName for j in jobs[1:]],
                         ["film ' é 1T", "film ' é 1", "film ' é 2T", "film ' é 2"])
        for i, job in enumerate(jobs[1:]):
            self.assertEqual(job.settings.VideoQuality, 'High')
            self.assertTrue(job.settings.ExportAudio)
            self.assertEqual(job.timeline.video.fades.FadeOut, 24 if i % 2 == 0 else 0)
            self.assertEqual(job.timeline.audio.fades.FadeOut, 24 if i % 2 == 0 else 0)
            self.assertEqual(job.timeline.count, 720 if i % 2 == 0 else 960)
        self.assertEqual(state.current.name, 'Original edit')
        self.assertTrue((self.folder / 'queued.txt').exists())

    def test_failure_rolls_back_only_this_batch(self):
        self.lua.globals().state.fail_at = 3
        with self.assertRaisesRegex(LuaError, 'rolled back'):
            self.lua.execute(self.code)
        state = self.lua.globals().state
        self.assertEqual(len(state.jobs), 1)
        self.assertEqual(state.jobs[1].id, 'existing')
        self.assertEqual(len(state.timelines), 1)
        self.assertEqual(state.current.name, 'Original edit')
        self.assertFalse((self.folder / 'queued.txt').exists())

    def test_second_run_refused_without_changes(self):
        self.lua.execute(self.code)
        with self.assertRaisesRegex(LuaError, 'already queued'):
            self.lua.execute(self.code)
        self.assertEqual(len(self.lua.globals().state.jobs), 5)

    def test_fade_failure_never_silently_queues_unfaded_teaser(self):
        self.lua.globals().state.fade_failure = True
        with self.assertRaisesRegex(LuaError, 'Cannot set video fade'):
            self.lua.execute(self.code)
        self.assertEqual(len(self.lua.globals().state.jobs), 1)

    def test_output_collision_refused(self):
        (self.root / "film ' é 1.mp4").write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'Output already exists'):
            prepare_queue(self.source, self.root, {'fps': Fraction(24)}, [(5, 45)], 1, 1, 'Audio Sections High')
