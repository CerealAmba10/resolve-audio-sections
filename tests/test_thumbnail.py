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

    def test_mirrored_layout_puts_title_right_and_label_left(self):
        frame = thumbnail.render(self.background, 'Good girl', 'TEASER: 3', 640, 360, layout=thumbnail.mirrored(None))
        pink = [x for y in range(0, 180, 3) for x in range(0, 640, 3)
                if (lambda p: p[0] > 230 and 50 < p[1] < 110 and p[2] > 170)(frame.getpixel((x, y)))]
        cyan = [x for y in range(0, 90, 3) for x in range(0, 640, 3)
                if (lambda p: p[0] < 80 and p[1] > 150 and p[2] > 230)(frame.getpixel((x, y)))]
        self.assertTrue(pink and max(pink) > 640 * 0.85 and sum(pink) / len(pink) > 640 * 0.5)
        self.assertTrue(cyan and max(cyan) < 640 * 0.4)

    def test_logo_and_extra_text_are_placed_and_can_be_hidden(self):
        logo = self.folder / 'logo.png'
        Image.new('RGBA', (200, 100), (255, 200, 0, 255)).save(logo)
        layout = thumbnail.layout_with(text=dict(value='GEMINI', x=0.2, y=0.85), logo=dict(x=0.8, y=0.8, angle=10))
        frame, boxes = thumbnail.compose(self.background, 'Hello', 'TEASER: 1', 640, 360, layout, logo)
        self.assertEqual(set(boxes), {'title', 'label', 'logo', 'text'})
        left, top, right, bottom = boxes['text']
        self.assertAlmostEqual((left + right) / 2 / 640, 0.2, delta=0.01)
        self.assertEqual(frame.getpixel((round(0.8 * 640), round(0.8 * 360))), (255, 200, 0))   # logo centred there
        hidden = thumbnail.layout_with(layout, logo=dict(visible=False))
        self.assertNotIn('logo', thumbnail.compose(self.background, 'Hello', '', 640, 360, hidden, logo)[1])

    def test_extra_text_takes_three_lines_and_stays_in_frame(self):
        def text_box(value):
            layout = thumbnail.layout_with(text=dict(value=value, x=0.2, y=0.95))
            return thumbnail.compose(self.background, '', '', 640, 360, layout)[1]['text']
        one, three, four = text_box('GEMINI'), text_box('DOMI\nGEMINI\nANAL'), text_box('DOMI\nGEMINI\nANAL\nMORE')
        self.assertGreater(three[3] - three[1], 2.5 * (one[3] - one[1]) * 0.8)    # three stacked lines
        self.assertEqual(three, four)                                             # a fourth line is ignored
        self.assertLessEqual(three[3], 360 + (three[3] - three[1]) * 0.2)        # nudged up into the frame

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


    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg on PATH')
    def test_full_clip_with_thumbnail_fades_only_at_its_end(self):
        clip = self.folder / 'no count.mkv'                    # Matroska keeps no frame count in its header
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=white:s=320x180:r=24:d=6',
                        '-c:v', 'libx264', str(clip)], check=True)
        self.assertEqual(media.frame_count(clip), 144)
        thumb = self.folder / 'thumb dark.png'
        Image.new('RGB', (320, 180), (0, 0, 0)).save(thumb)
        media.finish_full(clip, None, Fraction(24), 1.0, thumb, 1.0)

        def luma(at):
            raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(clip), '-map', '0:v:0', '-frames:v', '1',
                                  '-vf', 'scale=8:8,format=gray', '-f', 'rawvideo', '-'], capture_output=True).stdout
            return sum(raw) / len(raw)
        self.assertLess(luma(0.5), 30)          # the (black) thumbnail
        self.assertGreater(luma(3.0), 200)      # the clip itself, not faded
        self.assertLess(luma(5.95), 60)         # fading out at the very end

if __name__ == '__main__':
    unittest.main()
