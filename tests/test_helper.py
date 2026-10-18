import http.server
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import organizer
from firefox_bidi import find_firefox, launch

try:
    find_firefox()
    FIREFOX = True
except RuntimeError:
    FIREFOX = False

EXTENSION = Path(__file__).resolve().parent.parent / 'helper'
# Stand-in post form: a site button that clicks a hidden file input, like the real sites do.
FORM = (b'<input type="file" id="up" accept="video/*" style="display:none"><button id="pick">Upload</button>'
        b'<button id="post">Post</button><textarea id="cap" style="width:400px;height:100px"></textarea>'
        b'<script>pick.onclick = () => up.click(); up.onchange = async () => document.title = "got " +'
        b' up.files[0].name + " " + (await up.files[0].text()); post.onclick = () => window.posted = true;</script>')
VIEWS = iter(range(100, 10000, 25))
STATS_PAGE = (b'<script>(async () => { const r = await fetch("/api/v1/account/stats/412345678901234567?days=7");'
              b' const m = await fetch("/api/v1/messages/stats"); document.title = "views " + (await r.json()).response.views;'
              b' })()</script>')


class Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        if self.path.startswith('/api/v1/'):
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            secret = 'DM-SECRET' if 'messages' in self.path else ''
            self.wfile.write(json.dumps({'success': True, 'response': {
                'views': 0 if secret else next(VIEWS), 'tips': 5, 'note': secret}}).encode())
            return
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<!doctype html><body>' + (STATS_PAGE if self.path == '/stats-page' else FORM))

    def log_message(self, *args):
        pass


@unittest.skipIf(not FIREFOX, 'Firefox not installed')
class PostHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        clips = root / 'clips'
        clips.mkdir()
        for name, data in (('SFW 1.mp4', b'CLIP-ONE'), ('SFW 2.mp4', b'CLIP-TWO')):
            (clips / name).write_bytes(data)
        # Clip Organizer with one batch planned for Fansly.
        self.app = organizer.App(root / 'data', probe=lambda p: dict(duration=30, width=1080, height=1920))
        folder = self.app.post('/api/folders', {'path': str(clips)}, None)['path']
        self.app.post('/api/batch', {'folder': folder, 'names': ['SFW 1.mp4', 'SFW 2.mp4'], 'platforms': ['Fansly']}, None)
        self.servers = [organizer.serve(self.app, port=0), http.server.ThreadingHTTPServer(('127.0.0.1', 0), Site)]
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.site = f'http://127.0.0.1:{self.servers[1].server_port}'
        # Test copy of the extension: the local site stands in for fansly.com, the test app for port 47833.
        ext = root / 'ext'
        shutil.copytree(EXTENSION, ext)
        manifest = json.loads((ext / 'manifest.json').read_text())
        manifest['content_scripts'][0]['matches'].append('http://127.0.0.1/*')
        (ext / 'manifest.json').write_text(json.dumps(manifest))
        for name, old, new in (('sites.js', "'Fansly': 'fansly.com'", "'Fansly': '127.0.0.1'"),
                               ('store.js', 'http://127.0.0.1:47833', f'http://127.0.0.1:{self.servers[0].server_port}'),
                               ('stats-capture.js', "['*://apiv3.fansly.com/api/v1/*']", "['http://127.0.0.1/api/v1/*']")):
            (ext / name).write_text((ext / name).read_text().replace(old, new))
        self.process, self.bidi = launch(root / 'profile')
        self.bidi.call('webExtension.install', extensionData={'type': 'path', 'path': str(ext)})
        time.sleep(1.5)
        self.panel = self.context('/helper.html')

    def tearDown(self):
        self.bidi.sock.close()
        self.process.kill()
        self.process.wait()
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.app.db.close()
        self.temp.cleanup()

    def contexts(self, tree=None):
        for c in tree if tree is not None else self.bidi.call('browsingContext.getTree')['contexts']:
            yield c
            yield from self.contexts(c.get('children') or [])

    def context(self, suffix, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = [c['context'] for c in self.contexts() if c['url'].split('?')[0].endswith(suffix)]
            if found:
                return found[0]
            time.sleep(0.3)
        self.fail('no page ' + suffix)

    def js(self, context, code):
        result = self.bidi.call('script.evaluate', expression=code, target={'context': context}, awaitPromise=True,
                                resultOwnership='root')
        if 'result' not in result:
            raise AssertionError(result.get('exceptionDetails', result))
        return result['result'].get('value')

    def wait(self, context, code, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.js(context, code)
            if value:
                return value
            time.sleep(0.3)
        self.fail('timed out waiting for: ' + code)

    def test_upload_click_opens_picker_and_planned_clip_fills_the_form(self):
        self.assertIn('Connected', self.wait(self.panel, "document.getElementById('status').classList.contains('on') && "
                                                         "document.getElementById('status').textContent"))
        page = self.bidi.call('browsingContext.create', type='tab')['context']
        self.bidi.call('browsingContext.navigate', context=page, url=self.site + '/compose', wait='complete')
        time.sleep(0.5)
        self.js(page, 'pick.click()')  # the site's own upload button
        picker = self.context('/picker.html')
        tiles = self.wait(picker, "document.querySelectorAll('.tile').length >= 2 && document.getElementById('body').innerText")
        self.assertIn('Planned for Fansly', tiles)
        self.assertIn('Latest batch', tiles)
        self.js(picker, "document.querySelector('.tile').click()")  # first planned post
        self.assertEqual(self.wait(page, "document.title.startsWith('got') && document.title"), 'got SFW 1.mp4 CLIP-ONE')
        self.assertEqual(self.js(page, 'cap.value'), 'SFW 1')  # caption from the planner template
        card = "document.getElementById('post-helper-card').shadowRoot"
        self.assertIn('Caption filled', self.js(page, f"{card}.querySelector('.note').textContent"))
        self.assertFalse(self.js(page, '!!window.posted'))  # Post is always the creator's click
        self.js(page, f"{card}.querySelector('[data-act=posted]').click()")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self.app.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0]:
            time.sleep(0.3)
        post = self.app.db.execute('SELECT p.platform, p.source, c.name FROM posts p JOIN clips c ON c.fp=p.fp').fetchone()
        self.assertEqual(tuple(post), ('Fansly', 'post-helper', 'SFW 1.mp4'))
        self.assertEqual(len(self.app.picker('Fansly')['planned']), 1)  # that plan is done

    def test_saves_fansly_stats_quietly_and_never_messages(self):
        page = self.bidi.call('browsingContext.create', type='tab')['context']
        seen = []
        for _ in range(3):  # three visits to the stats page on different days
            self.bidi.call('browsingContext.navigate', context=page, url=self.site + '/stats-page', wait='complete')
            seen.append(self.wait(page, "document.title.startsWith('views') && document.title"))
            time.sleep(0.5)
        self.assertEqual(len(set(seen)), 3)  # the page still gets each real response
        saved = json.loads(self.wait(self.panel, "stats('readonly', s => s.getAll()).then(a => a.length === 3 && JSON.stringify(a))"))
        self.assertEqual({s['path'] for s in saved}, {'/api/v1/account/stats/{id}'})  # DMs are never saved
        shapes = self.js(self.panel, "JSON.stringify(shape({response: {views: 9, '4123': {name: 'Fan'}}, list: [{tips: 1}]}))")
        self.assertEqual(json.loads(shapes), {'response': {'views': 'number', '{id}': {'name': 'string'}},
                                              'list': [{'tips': 'number'}]})  # keys and types only


if __name__ == '__main__':
    unittest.main()
