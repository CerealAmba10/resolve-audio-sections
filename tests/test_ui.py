import tempfile
import time
import tkinter as tk
import unittest
import json
from unittest.mock import patch

from app import App
import test_media as fixtures


class DesktopTests(unittest.TestCase):
    def test_project_request_automatically_scans_and_prepares(self):
        fixtures.MediaTests.setUpClass()
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as output:
                app = App(root, project_folder=output)
                app.vars['output'].set(output)
                request = app.project_folder / 'request.otio'
                request.write_text(json.dumps({'media_reference': {'target_url': fixtures.MediaTests.video.as_uri()}}))
                with patch('app.messagebox.showerror', side_effect=AssertionError):
                    app.poll()
                    deadline = time.monotonic() + 15
                    while app.batch is None and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertIsNotNone(app.batch)
                    self.assertIn('queue.lua', (app.project_folder / 'latest.lua').read_text())
        finally:
            root.destroy()
            fixtures.MediaTests.tearDownClass()

    def test_scan_prepare_and_clipboard_flow(self):
        # Use the same real-media fixture generation as the scanner tests.
        fixtures.MediaTests.setUpClass()
        root = tk.Tk()
        root.withdraw()
        try:
            with tempfile.TemporaryDirectory() as output:
                app = App(root, project_folder=output)
                app.vars['source'].set(str(fixtures.MediaTests.video))
                app.vars['output'].set(output)
                with patch('app.messagebox.showerror', side_effect=AssertionError):
                    app.scan()
                    deadline = time.monotonic() + 15
                    while app.busy and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertFalse(app.busy)
                    self.assertEqual(len(app.table.get_children()), 2)
                    app.prepare()
                    command = root.clipboard_get()
                    self.assertTrue(command.startswith('dofile('))
                    self.assertIn('queue.lua', command)
                    self.assertTrue((app.batch / 'manifest.json').exists())
        finally:
            root.destroy()
            fixtures.MediaTests.tearDownClass()
