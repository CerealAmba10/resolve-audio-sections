"""Real FFmpeg checks: channel routing across streams and placement on timeline time."""
import shutil
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import detection as d
import media


@unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg on PATH')
class MediaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Stream 0 (stereo): tone only during 4-6 s. Stream 1 (mono): steady noise the whole time.
        cls.folder = Path(tempfile.mkdtemp())
        cls.path = cls.folder / 'two streams.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=440:d=10,volume=0.5,'
                        "volume='between(t,4,6)':eval=frame,aformat=channel_layouts=stereo",
                        '-f', 'lavfi', '-i', 'anoisesrc=d=10:a=0.1,aformat=channel_layouts=mono',
                        '-map', '0', '-map', '1', '-c:a', 'pcm_s16le', str(cls.path)], check=True)

    def active(self, mapping, source=0.0, at=24, frames=240):
        item = dict(path=str(self.path), start=at, end=at + frames, source_seconds=source, mapping=mapping)
        env = media.track_envelope(dict(items=[item]), dict(fps=Fraction(24), start=0, end=at + frames))
        return [(round(a, 1), round(b, 1)) for a, b in d.active_runs([env], [-40])]

    def test_mapping_selects_stream_channels(self):
        voice = '{"track_mapping":{"1":{"channel_idx":[1,2],"mute":false,"type":"Stereo"}}}'
        noise = '{"track_mapping":{"1":{"channel_idx":[3],"mute":false,"type":"Mono"}}}'
        self.assertEqual(self.active(voice, frames=216), [(5.0, 7.0)])   # item placed 1 s into the timeline
        self.assertEqual(self.active(noise, frames=216), [(1.0, 10.0)])
        self.assertEqual(self.active('{"track_mapping":{"1":{"channel_idx":[1,2],"mute":true}}}'), [])

    def test_source_offset_and_unknown_mapping(self):
        # Item uses source 3-8 s at timeline 1 s, so the tone (source 4-6 s) lands at 2-4 s.
        self.assertEqual(self.active(None, source=3.0, frames=120), [(1.0, 6.0)])   # all channels incl. noise
        voice = '{"track_mapping":{"1":{"channel_idx":[1,2],"mute":false,"type":"Stereo"}}}'
        self.assertEqual(self.active(voice, source=3.0, frames=120), [(2.0, 4.0)])

    def test_still_becomes_video_of_exact_length(self):
        image = self.folder / 'wide still.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=3000x500:d=1', '-frames:v', '1', str(image)], check=True)
        video = media.still_video(image, 1500, Fraction(24), 320, 180, self.folder / 'stills/wide.mp4')   # 1 minute + 60 frames
        probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_entries',
                                'stream=nb_read_frames,width,height', '-of', 'csv=p=0', str(video)], capture_output=True, text=True)
        self.assertEqual(probe.stdout.strip(), '320,180,1500')
        self.assertEqual(media.still_video(image, 1500, Fraction(24), 320, 180, video), video)   # cached
        self.assertEqual([p.name for p in (self.folder / 'stills').iterdir()], ['wide.mp4'])  # no leftovers


if __name__ == '__main__':
    unittest.main()
