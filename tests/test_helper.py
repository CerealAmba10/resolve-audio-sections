import http.server
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from firefox_bidi import find_firefox, launch
from scheduler import DEFAULTS, stage

try:
    find_firefox()
    FIREFOX = True
except RuntimeError:
    FIREFOX = False

EXTENSION = Path(__file__).resolve().parent.parent / 'helper'
# Stand-in post forms: a plain textarea, and a rich-text editor (contenteditable).
FORM = (b'<input type="file" id="up" accept="video/*" style="display:none"><button id="post">Post</button>'
        b'<script>up.onchange = async () => document.title = "got " + up.files[0].name + " " +'
        b' (await up.files[0].text()); post.onclick = () => window.posted = true;</script>')
PAGES = {'/compose': FORM + b'<textarea id="cap" style="width:400px;height:100px"></textarea>',
         '/compose-rich': FORM + b'<div id="cap" contenteditable="true" style="width:400px;height:100px"></div>'}
SERVED = {}  # /files/<n> -> exported file, so the import page can be handed the same files a person picks
VIEWS = iter(range(100, 10000, 25))  # a stats value that grows between visits
STATS_PAGE = (b'<script>(async () => { const r = await fetch("/api/v1/account/stats/412345678901234567?days=7");'
              b' const m = await fetch("/api/v1/messages/stats"); document.title = "views " + (await r.json()).response.views;'
              b' })()</script>')


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        if self.path.startswith('/api/v1/'):
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            secret = 'DM-SECRET' if 'messages' in self.path else ''
            self.wfile.write(json.dumps({'success': True, 'response': {
                'views': 0 if secret else next(VIEWS), 'tips': 5, 'accountId': '412345678901234567', 'createdAt': 1, 'note': secret}}).encode())
            return
        if self.path == '/stats-page':
            self.end_headers()
            self.wfile.write(STATS_PAGE)
            return
        if self.path in SERVED:
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(SERVED[self.path].read_bytes())
            return
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<!doctype html><body>' + PAGES.get(self.path, b''))

    def log_message(self, *args):
        pass


@unittest.skipIf(not FIREFOX, 'Firefox not installed')
class PostHelperTests(unittest.TestCase):
    def test_import_batch_fill_forms_and_advance(self):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, data in (('rec 1.mp4', b'CLIP-ONE'), ('rec 2.mp4', b'CLIP-TWO'), ('rec 1T.mp4', b'TEASER')):
                (folder / name).write_bytes(data)
            options = json.dumps(DEFAULTS['Fansly'])
            exported = stage([
                dict(platform='Fansly', file=str(folder / 'rec 2.mp4'), at='2026-01-02 20:00', caption='Second', options=options),
                dict(platform='Fansly', file=str(folder / 'rec 1.mp4'), at='2026-01-02 12:00', caption='First <b>&', options=options),
                dict(platform='TikTok', file=str(folder / 'rec 1T.mp4'), at='2026-01-02 17:00', caption='T',
                     options=json.dumps(DEFAULTS['TikTok']))], folder / 'batches')
            process, bidi, panel = self.start(folder)
            run, wait = self.js, self.wait
            try:
                # Automation can't use the file picker on extension pages, so hand the import input the same
                # files a person would select, then let the page's real import handler run.
                chosen = [p for p in exported['Fansly'].iterdir()] + [exported['TikTok'] / 'batch.json']
                SERVED.update({f'/files/{i}': path for i, path in enumerate(chosen)})
                listing = json.dumps([[base + f'/files/{i}', path.name] for i, path in enumerate(chosen)])
                run(panel, f"""(async () => {{
                    const transfer = new DataTransfer();
                    for (const [url, name] of {listing}) transfer.items.add(new File([await (await fetch(url)).blob()], name));
                    const input = document.getElementById('import');
                    input.files = transfer.files;
                    input.dispatchEvent(new Event('change'));
                }})()""")
                message = wait(panel, "document.getElementById('message').textContent")
                self.assertIn('Imported 2 posts', message)
                self.assertIn('TikTok posts are uploaded by hand', message)

                page = bidi.call('browsingContext.create', type='tab')['context']
                card = "document.getElementById('post-helper-card')?.shadowRoot"
                bidi.call('browsingContext.navigate', context=page, url=base + '/compose', wait='complete')
                text = wait(page, f"{card}?.textContent")
                self.assertIn('1 of 2', text)
                self.assertIn('rec 1.mp4', text)  # earliest planned time first
                self.assertIn('First <b>&', text)  # shown as text, never as page markup
                self.assertIn('subscribers', text)  # launch options listed
                run(page, f"{card}.querySelector('[data-act=fill]').click()")
                self.assertEqual(wait(page, "document.title.startsWith('got') && document.title"), 'got rec 1.mp4 CLIP-ONE')
                self.assertEqual(run(page, 'cap.value'), 'First <b>&')
                self.assertIn('Clip attached. Caption filled.', run(page, f"{card}.querySelector('.note').textContent"))
                self.assertFalse(run(page, '!!window.posted'))  # the creator clicks Post, never the helper
                run(page, f"{card}.querySelector('[data-act=posted]').click()")
                self.assertIn('2 of 2', wait(page, f"{card}.textContent.includes('2 of 2') && {card}.textContent"))

                bidi.call('browsingContext.navigate', context=page, url=base + '/compose-rich', wait='complete')
                wait(page, f"{card}?.textContent.includes('rec 2.mp4')")
                run(page, f"{card}.querySelector('[data-act=fill]').click()")
                self.assertEqual(wait(page, "document.title.startsWith('got') && document.title"), 'got rec 2.mp4 CLIP-TWO')
                self.assertEqual(run(page, 'cap.innerText'), 'Second')
                run(page, f"{card}.querySelector('[data-act=posted]').click()")
                wait(page, "!document.getElementById('post-helper-card')")  # batch finished: card goes away
                statuses = run(panel, "browser.storage.local.get('posts').then(r => r.posts.map(p => p.status).join())")
                self.assertEqual(statuses, 'posted,posted')
            finally:
                bidi.sock.close()
                process.kill()
                process.wait()
                server.shutdown()
                server.server_close()

    def test_saves_fansly_stats_quietly_and_never_messages(self):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        with tempfile.TemporaryDirectory() as folder:
            process, bidi, panel = self.start(Path(folder))
            run, wait = self.js, self.wait
            try:
                page = bidi.call('browsingContext.create', type='tab')['context']
                seen = []
                for _ in range(3):  # three visits to the stats page on different days
                    bidi.call('browsingContext.navigate', context=page, url=base + '/stats-page', wait='complete')
                    seen.append(wait(page, "document.title.startsWith('views') && document.title"))
                    time.sleep(0.5)
                self.assertEqual(len(set(seen)), 3)  # the page still gets each real response
                saved = json.loads(wait(panel, "stats('readonly', s => s.getAll()).then(a => a.length === 3 && JSON.stringify(a))"))
                self.assertEqual({s['path'] for s in saved}, {'/api/v1/account/stats/{id}'})  # DMs are never saved
                self.assertEqual([s['data']['response']['views'] for s in saved],
                                 sorted(int(t.split()[1]) for t in seen))  # one reading per visit, in order
                shapes = run(panel, "JSON.stringify(shape({response: {views: 9, '4123': {name: 'Fan'}}, list: [{tips: 1}]}))")
                self.assertEqual(json.loads(shapes), {'response': {'views': 'number', '{id}': {'name': 'string'}},
                                                      'list': [{'tips': 'number'}]})  # keys and types only
            finally:
                bidi.sock.close()
                process.kill()
                process.wait()
                server.shutdown()
                server.server_close()

    def start(self, folder):
        # Test copy: the local server stands in for fansly.com and its API.
        ext = folder / 'ext'
        shutil.copytree(EXTENSION, ext)
        manifest = json.loads((ext / 'manifest.json').read_text())
        manifest['content_scripts'][0]['matches'].append('http://127.0.0.1/*')
        manifest['permissions'].append('http://127.0.0.1/*')
        (ext / 'manifest.json').write_text(json.dumps(manifest))
        sites = ext / 'sites.js'
        sites.write_text(sites.read_text().replace("'Fansly': 'fansly.com'", "'Fansly': '127.0.0.1'"))
        capture = ext / 'stats-capture.js'
        capture.write_text(capture.read_text().replace("['*://apiv3.fansly.com/api/v1/*']", "['http://127.0.0.1/api/v1/*']"))
        process, bidi = launch(folder / 'profile')
        self.bidi = bidi
        bidi.call('webExtension.install', extensionData={'type': 'path', 'path': str(ext)})
        time.sleep(1.5)
        panel = next(c['context'] for c in bidi.call('browsingContext.getTree')['contexts'] if c['url'].endswith('/helper.html'))
        return process, bidi, panel

    def js(self, context, code):
        result = self.bidi.call('script.evaluate', expression=code, target={'context': context}, awaitPromise=True,
                                resultOwnership='root')['result']
        return result if result.get('type') == 'node' else result.get('value')

    def wait(self, context, code, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.js(context, code)
            if value:
                return value
            time.sleep(0.3)
        self.fail('timed out waiting for: ' + code)


if __name__ == '__main__':
    unittest.main()
