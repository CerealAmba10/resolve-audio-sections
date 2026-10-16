import unittest
from fractions import Fraction

from audio_sections import sections_from_silence, intersect_silences, frame_jobs, lua_string


class SectionTests(unittest.TestCase):
    def test_gaps_excluded_and_only_over_thirty(self):
        self.assertEqual(sections_from_silence(150, [(0, 5), (45, 55), (85, 90), (140, 150)]),
                         [(5, 45), (90, 140)])

    def test_no_gaps_and_all_silent(self):
        self.assertEqual(sections_from_silence(31, []), [(0, 31)])
        self.assertEqual(sections_from_silence(31, [(0, 31)]), [])
        self.assertEqual(sections_from_silence(30, []), [])

    def test_trailing_audio_kept(self):
        self.assertEqual(sections_from_silence(95, [(0, 10), (50, 60)]), [(10, 50), (60, 95)])

    def test_all_tracks_must_be_silent(self):
        self.assertEqual(intersect_silences([[(0, 10), (40, 50)], [(5, 15), (45, 55)]]),
                         [(5, 10), (45, 50)])
        self.assertEqual(intersect_silences([[(0, 10)], []]), [])

    def test_pair_order_names_and_inclusive_frames(self):
        jobs = frame_jobs('my film.mp4', [(10, 70), (80, 121)], Fraction(24), 7, 1)
        self.assertEqual([j['name'] for j in jobs], ['my film 7T', 'my film 7', 'my film 8T', 'my film 8'])
        self.assertEqual((jobs[0]['start_frame'], jobs[0]['end_frame']), (240, 959))
        self.assertEqual(jobs[0]['fade_seconds'], 1)
        self.assertEqual(jobs[1]['fade_seconds'], 0)
        self.assertEqual(jobs[1]['end_frame'], 1679)

    def test_fractional_fps_teaser_is_nearest_frame(self):
        jobs = frame_jobs('film.mov', [(1.1, 80)], Fraction(30000, 1001), 1, 1)
        count = jobs[0]['end_frame'] - jobs[0]['start_frame'] + 1
        self.assertLess(abs(count / float(Fraction(30000, 1001)) - 30), 0.034)

    def test_lua_escaping(self):
        self.assertEqual(lua_string('x"\\\n'), '"x\\"\\\\\\010"')


if __name__ == '__main__':
    unittest.main()
