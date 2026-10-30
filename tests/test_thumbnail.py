"""Thumbnail frames: layout, title dealing, and videos opening on them (real FFmpeg)."""
import random
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from PIL import Image

import media
import thumbnail


class ThumbnailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp())
        cls.background = cls.folder / 'background.png'
        Image.new('RGB', (800, 600), (20, 30, 90)).save(cls.background)

    def test_titles_are_used_up_before_repeating(self):
        dealt = thumbnail.deal_titles(['a', 'b', 'c'], 7, random.Random(3))
        self.assertEqual(sorted(dealt[:3]), ['a', 'b', 'c'])
        self.assertEqual(sorted(dealt[3:6]), ['a', 'b', 'c'])
        self.assertEqual(thumbnail.deal_titles([], 2), [])

    def test_title_top_left_label_top_right(self):
        frame = thumbnail.render(self.background, 'Drain yourself for mommy', 'TEASER: 29', 640, 360)
        self.assertEqual(frame.size, (640, 360))
        pink = [(x, y) for y in range(0, 360, 3) for x in range(0, 640, 3)
                if (lambda p: p[0] > 230 and 50 < p[1] < 110 and p[2] > 170)(frame.getpixel((x, y)))]
        cyan = [(x, y) for y in range(0, 360, 3) for x in range(0, 640, 3)
                if (lambda p: p[0] < 80 and p[1] > 150 and p[2] > 230)(frame.getpixel((x, y)))]
        self.assertTrue(pink and max(x for x, _ in pink) < 640 * 0.55 and max(y for _, y in pink) < 360 * 0.55)
        self.assertTrue(cyan and min(x for x, _ in cyan) > 640 * 0.6 and max(y for _, y in cyan) < 360 * 0.25)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg on PATH')
    def test_teaser_opens_on_thumbnail_then_shows_the_clip(self):
        full = self.folder / 'full.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=24:d=4',
                        '-f', 'lavfi', '-i', 'sine=d=4', '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(full)], check=True)
        thumb = self.folder / 'thumb.png'
        thumbnail.render(self.background, 'Hello', 'TEASER: 1', 320, 180).save(thumb)
        teaser = self.folder / 'teaser.mp4'
        media.make_teaser(full, teaser, 48, Fraction(24), 0.5, thumb, 1.0)

        def frame(path, n):
            return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-vf',
                                   f'select=eq(n\\,{n}),scale=16:9,format=gray', '-frames:v', '1', '-f', 'rawvideo', '-'],
                                  capture_output=True).stdout
        picture = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(thumb), '-vf', 'scale=16:9,format=gray', '-f',
                                  'rawvideo', '-'], capture_output=True).stdout
        diff = lambda a, b: sum(abs(x - y) for x, y in zip(a, b)) / len(a)
        self.assertLess(diff(frame(teaser, 0), picture), 8)       # 1 s of thumbnail…
        self.assertLess(diff(frame(teaser, 23), picture), 8)
        self.assertLess(diff(frame(teaser, 26), frame(full, 26)), 8)   # …then the clip itself
        streams = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name:stream_disposition=attached_pic',
                                  '-of', 'csv=p=0', str(teaser)], capture_output=True, text=True).stdout.split()
        self.assertIn('mjpeg,1', streams)                             # and it is the cover image


if __name__ == '__main__':
    unittest.main()
