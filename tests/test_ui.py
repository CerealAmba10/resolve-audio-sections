import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app import App
import test_media as fixtures


class DesktopTests(unittest.TestCase):
    def test_scan_prepare_and_clipboard_flow(self):
        # Use the same real-media fixture generation as the scanner tests.
        fixtures.MediaTests.setUpClass()
        root = tk.Tk()
        root.withdraw()
        try:
            app = App(root)
            with tempfile.TemporaryDirectory() as output:
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
