import json
import tempfile
import unittest
from pathlib import Path

from project_tools import read_project_request, write_project_tools, publish_batch


class ProjectToolsTests(unittest.TestCase):
    def test_timeline_video_audio_references_resolve_to_one_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / 'recording é.mp4'
            source.write_bytes(b'source')
            request = folder / 'request.otio'
            request.write_text(json.dumps({'tracks': [
                {'media_reference': {'target_url': source.as_uri()}},
                {'media_reference': {'target_url': source.as_uri()}}]}), encoding='utf-8')
            self.assertEqual(read_project_request(request), source)
            script = write_project_tools(folder)
            self.assertIn('AudioSectionsAnalyze', script.read_text())
            self.assertIn('EXPORT_OTIO', script.read_text())
            publish_batch(folder, folder / 'batch')
            self.assertIn('batch/queue.lua', (folder / 'latest.lua').read_text())

    def test_multiple_sources_are_not_silently_guessed(self):
        with tempfile.TemporaryDirectory() as temporary:
            request = Path(temporary) / 'request.otio'
            request.write_text(json.dumps([{'target_url': 'file:///C:/a.mp4'},
                                           {'target_url': 'file:///C:/b.mp4'}]))
            with self.assertRaisesRegex(ValueError, 'one source'):
                read_project_request(request)
