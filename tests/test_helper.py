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
FORM = (b'<input type="file" id="up" accept="video/*" multiple style="display:none"><button id="pick">Upload</button>'
        b'<button id="post">Post</button><textarea id="cap" style="width:400px;height:100px"></textarea>'
        b'<script>pick.onclick = () => up.click(); up.onchange = async () => document.title = "got " +'
        b' [...up.files].map(f => f.name).join(",") + " " + (await up.files[0].text());'
        b' post.onclick = () => { window.posted = true; fetch("/api/v1/post", {method: "POST", body: "{}"}); };</script>')
LIKES = iter(range(5, 1000))
# Stand-in Fansly pages: the profile loads the account, its timeline and a message thread; Post creates a post.
PROFILE = (b'<script>(async () => { await fetch("/api/v1/account/me"); const t = await fetch("/api/v1/timelinenew/412345678901234567");'
           b' await fetch("/api/v1/messages/stats"); document.title = "seen " + (await t.json()).response.posts[0].likeCount;'
           b' })()</script>')


class Site(http.server.BaseHTTPRequestHandler):
    def send_body(self, body, kind='application/json'):
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

    def do_GET(self):
        if self.path == '/api/v1/account/me':
            return self.send_body({'success': True, 'response': {'account': {'id': '412345678901234567', 'username': 'ducky'}}})
        if self.path.startswith('/api/v1/timelinenew/'):
            return self.send_body({'success': True, 'response': {'posts': [
                {'id': '900', 'accountId': '412345678901234567', 'createdAt': 1790000000, 'likeCount': next(LIKES), 'tipAmount': 200,
                 'content': 'caption'},
                {'id': '777', 'accountId': '222', 'createdAt': 1790000000, 'likeCount': 99}]}})  # someone else's
        if self.path.startswith('/api/v1/messages'):
            return self.send_body({'response': [{'id': '555', 'accountId': '412345678901234567', 'createdAt': 1, 'likeCount': 1,
                                                 'content': 'DM-SECRET'}]})
        self.send_body(b'<!doctype html><body>' + (PROFILE if self.path == '/profile' else FORM), 'text/html')

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        self.send_body({'success': True, 'response': {'id': '900', 'accountId': '412345678901234567'}})

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
        (ext / 'watcher.js').write_text((ext / 'watcher.js').read_text(encoding='utf-8').replace(
            "platform: 'Fansly'", "platform: 'Fansly'"), encoding='utf-8')
        for name, old, new in (('sites.js', "'Fansly': 'fansly.com'", "'Fansly': '127.0.0.1'"),
                               ('store.js', 'http://127.0.0.1:47833', f'http://127.0.0.1:{self.servers[0].server_port}'),
                               ('watcher.js', "['*://apiv3.fansly.com/api/v1/*']", "['http://127.0.0.1/api/v1/*']")):
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

    def test_attach_whole_batch_with_fbuddy_captions_and_log_all(self):
        page = self.bidi.call('browsingContext.create', type='tab')['context']
        self.bidi.call('browsingContext.navigate', context=page, url=self.site + '/compose', wait='complete')
        time.sleep(0.5)
        self.js(page, 'pick.click()')
        picker = self.context('/picker.html')
        self.wait(picker, "[...document.querySelectorAll('h2 button')].some(b => b.textContent === 'Attach all 2')")
        self.js(picker, "[...document.querySelectorAll('h2 button')].find(b => b.textContent === 'Attach all 2').click()")
        self.assertEqual(self.wait(page, "document.title.startsWith('got') && document.title"), 'got SFW 1.mp4,SFW 2.mp4 CLIP-ONE')
        card = "document.getElementById('post-helper-card').shadowRoot"
        captions = self.js(page, f"{card}.querySelector('[data-copy]').dataset.copy")
        self.assertEqual(captions, 'SFW 1\n---\nSFW 2')  # FBuddy "Paste texts" format, in clip order
        self.assertEqual(self.js(page, f"{card}.querySelector('[data-act=posted]').textContent"), 'I posted all 2')
        self.js(page, f"{card}.querySelector('[data-act=posted]').click()")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.app.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0] < 2:
            time.sleep(0.3)
        time.sleep(1)
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM posts').fetchone()[0], 2)  # each logged once

    def test_watcher_links_new_posts_and_tracks_own_post_numbers(self):
        page = self.bidi.call('browsingContext.create', type='tab')['context']
        self.bidi.call('browsingContext.navigate', context=page, url=self.site + '/compose', wait='complete')
        time.sleep(0.5)
        self.js(page, 'pick.click()')
        picker = self.context('/picker.html')
        self.wait(picker, "document.querySelectorAll('.tile').length >= 2")
        self.js(picker, "document.querySelector('.tile').click()")  # planned SFW 1
        self.wait(page, "document.title.startsWith('got')")
        self.js(page, 'post.click()')  # the creator posts; the site answers with the new post's ID
        fp = self.app.db.execute("SELECT fp FROM clips WHERE name='SFW 1.mp4'").fetchone()[0]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not self.app.db.execute('SELECT 1 FROM site_posts WHERE fp=?', (fp,)).fetchone():
            time.sleep(0.3)
        for visit in (1, 2):  # opening the profile again updates the numbers
            self.bidi.call('browsingContext.navigate', context=page, url=self.site + '/profile', wait='complete')
            likes = int(self.wait(page, "document.title.startsWith('seen') && document.title").split()[1])
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                totals, posts = self.app.detail(fp)['totals'], self.app.detail(fp)['site_posts']
                if totals.get('likeCount') == likes:
                    break
                time.sleep(0.3)
            self.assertEqual(totals, {'likeCount': likes, 'tipAmount': 200})
        self.assertEqual([p['post_id'] for p in posts], ['900'])
        ids = {r[0] for r in self.app.db.execute('SELECT post_id FROM site_posts')}
        self.assertEqual(ids, {'900'})  # not someone else's post, never the message thread
        shapes = json.loads(self.js(self.panel, "browser.storage.local.get('shapes').then(r => JSON.stringify(r.shapes))"))
        self.assertIn('/api/v1/timelinenew/{id}', shapes)
        self.assertEqual(shapes['/api/v1/timelinenew/{id}']['response']['posts'][0]['likeCount'], 'number')
        self.assertNotIn('/api/v1/messages/stats', shapes)
        self.assertNotIn('DM-SECRET', json.dumps(shapes))

if __name__ == '__main__':
    unittest.main()
