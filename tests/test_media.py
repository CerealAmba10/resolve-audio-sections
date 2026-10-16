import math
import tempfile
import unittest
import wave
from pathlib import Path
from threading import Event

from audio_sections import scan_media, Cancelled, prepare_queue


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.media = cls.root / "test ' unicode é.wav"
        with wave.open(str(cls.media), 'wb') as out:
            out.setparams((2, 2, 8000, 0, 'NONE', 'not compressed'))
            for second in range(102):
                active = 5 <= second < 45 or 55 <= second < 95
                samples = bytearray()
                for n in range(8000):
                    value = int(12000 * math.sin(2 * math.pi * 440 * n / 8000)) if active else 0
                    samples.extend(value.to_bytes(2, 'little', signed=True))
                    samples.extend((-value).to_bytes(2, 'little', signed=True))
                out.writeframes(samples)
        import subprocess
        cls.video = cls.root / 'film é.mkv'
        subprocess.run([str(Path('tools/ffmpeg.exe').resolve()), '-v', 'error', '-f', 'lavfi',
                        '-i', 'color=c=red:s=64x64:r=24:d=102', '-i', str(cls.media),
                        '-c:v', 'mpeg4', '-c:a', 'pcm_s16le', '-shortest', str(cls.video)], check=True)
        cls.multi = cls.root / 'multi.mkv'
        subprocess.run([str(Path('tools/ffmpeg.exe').resolve()), '-v', 'error', '-i', str(cls.video),
                        '-f', 'lavfi', '-i', r'aevalsrc=if(between(t\,45\,55)\,0.5*sin(2*PI*440*t)\,0):s=8000:d=102',
                        '-map', '0:v', '-map', '0:a', '-map', '1:a', '-c:v', 'copy', '-c:a', 'pcm_s16le',
                        str(cls.multi)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_scan_preserves_antiphase_audio_and_trims_gaps(self):
        info, sections = scan_media(self.video, -40, 1, 'all', Event(), lambda _: None)
        self.assertEqual(str(info['fps']), '24')
        self.assertEqual(len(sections), 2)
        for actual, expected in zip(sections, [(5, 45), (55, 95)]):
            self.assertAlmostEqual(actual[0], expected[0], delta=0.02)
            self.assertAlmostEqual(actual[1], expected[1], delta=0.02)
        folder = prepare_queue(self.video, self.root, info, sections, 1, 1, 'Audio Sections High')
        self.assertTrue((folder / 'queue.lua').is_file())
        self.assertTrue((folder / 'clips.csv').is_file())

    def test_cancel_and_missing_audio_rejected(self):
        cancelled = Event()
        cancelled.set()
        with self.assertRaises(Cancelled):
            scan_media(self.video, -40, 1, 'all', cancelled, lambda _: None)
        with self.assertRaisesRegex(ValueError, 'video file'):
            scan_media(self.media, -40, 1, 'all', Event(), lambda _: None)

    def test_multiple_audio_streams_bridge_a_gap(self):
        _, sections = scan_media(self.multi, -40, 1, 'all', Event(), lambda _: None)
        self.assertEqual(len(sections), 1)
        self.assertAlmostEqual(sections[0][0], 5, delta=0.02)
        self.assertAlmostEqual(sections[0][1], 95, delta=0.02)
        _, first_only = scan_media(self.multi, -40, 1, '0', Event(), lambda _: None)
        self.assertEqual(len(first_only), 2)

    def test_invalid_settings(self):
        for threshold, gap in [(0, 1), (-40, 0), (float('nan'), 1)]:
            with self.assertRaises(ValueError):
                scan_media(self.video, threshold, gap, 'all', Event(), lambda _: None)
