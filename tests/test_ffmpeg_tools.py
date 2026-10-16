import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import ffmpeg_tools


class ToolsTests(unittest.TestCase):
    def test_existing_tools_need_no_download(self):
        with tempfile.TemporaryDirectory() as folder:
            pair = {'ffmpeg': 'C:/installed/ffmpeg.exe', 'ffprobe': 'C:/installed/ffprobe.exe'}
            with patch('ffmpeg_tools.find_pair', return_value=pair), patch('ffmpeg_tools.download_archive') as network:
                self.assertEqual(ffmpeg_tools.ensure_tools(folder), pair)
                network.assert_not_called()
            self.assertEqual(json.loads((Path(folder) / 'ffmpeg-paths.json').read_text()), pair)

    def test_path_discovery_requires_both_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('ffmpeg_tools.shutil.which', side_effect=lambda name: 'C:/bin/' + name + '.exe'), \
                 patch('ffmpeg_tools.validate_pair', return_value=True):
                self.assertEqual(ffmpeg_tools.find_pair(folder)['ffprobe'], 'C:/bin/ffprobe.exe')

    def test_archive_rejects_changed_bytes_before_extracting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'changed.zip'
            archive.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'verification'):
                ffmpeg_tools.install_archive(archive, root)
            self.assertFalse((root / 'tools').exists())

    def test_archive_copies_only_named_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / 'download.zip'
            with zipfile.ZipFile(archive, 'w') as out:
                for filename in ('bin/ffmpeg.exe', 'bin/ffprobe.exe', 'LICENSE', 'README.txt', '../../bad.txt'):
                    out.writestr('release/' + filename, b'test')
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch('ffmpeg_tools.validate_pair', return_value=True):
                pair = ffmpeg_tools.install_archive(archive, root, checksum)
            self.assertTrue(Path(pair['ffmpeg']).exists())
            self.assertEqual({p.name for p in (root / 'tools').iterdir()},
                             {'ffmpeg.exe', 'ffprobe.exe', 'LICENSE', 'README.txt'})

    def test_runtime_uses_saved_external_path_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / 'external.exe'
            executable.touch()
            (root / 'ffmpeg-paths.json').write_text(json.dumps({'ffmpeg': str(executable)}))
            self.assertEqual(ffmpeg_tools.tool_path('ffmpeg', root), executable)

    def test_failed_download_does_not_publish_config(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('ffmpeg_tools.find_pair', return_value=None), \
                 patch('ffmpeg_tools.download_archive', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    ffmpeg_tools.ensure_tools(folder)
            self.assertFalse((Path(folder) / 'ffmpeg-paths.json').exists())
