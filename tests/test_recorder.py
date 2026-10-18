import http.server
import json
import shutil
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from pathlib import Path

from firefox_bidi import find_firefox, launch

try:
    find_firefox()
    FIREFOX = True
except RuntimeError:
    FIREFOX = False

EXTENSION = Path(__file__).resolve().parent.parent / 'recorder'
PAGES = {
    '/upload': b'<title>New post</title><input id="cap" aria-label="Caption"><div contenteditable="true" id="ed"></div>'
               b'<button id="go">Schedule post</button>',
    '/login': b'<input type="password" id="pw"><button id="in">Sign in</button>',
    '/start': b'<input type="email" id="em"><button id="next">Continue</button>',
}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(b'<!doctype html><body>' + PAGES.get(self.path.split('?')[0], b''))

    def log_message(self, *args):
        pass


@unittest.skipIf(not FIREFOX, 'Firefox not installed')
class RecorderExtensionTests(unittest.TestCase):
    def test_records_listed_sites_only_and_never_typed_text_or_sign_in_screens(self):
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_port
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            # Test copy: the local test server stands in for one of the listed sites.
            ext = folder / 'ext'
            shutil.copytree(EXTENSION, ext)
            manifest = json.loads((ext / 'manifest.json').read_text())
            manifest['content_scripts'][0]['matches'].append('http://127.0.0.1/*')
            (ext / 'manifest.json').write_text(json.dumps(manifest))
            sites = ext / 'sites.js'
            sites.write_text(sites.read_text().replace("'pornhub.com'];", "'pornhub.com', '127.0.0.1'];"))
            downloads, ext_uuid = folder / 'downloads', str(uuid.uuid4())
            downloads.mkdir()
            process, bidi = launch(folder / 'profile', prefs={
                'extensions.webextensions.uuids': json.dumps({manifest['browser_specific_settings']['gecko']['id']: ext_uuid}),
                'browser.download.dir': str(downloads), 'browser.download.folderList': 2,
                'browser.download.useDownloadDir': True, 'browser.download.always_ask_before_handling_new_types': False})
            try:
                bidi.call('webExtension.install', extensionData={'type': 'path', 'path': str(ext)})
                # Let the extension's own start tab open first, so it can't take focus from the test tab later.
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and not any(c['url'].startswith('moz-extension://')
                                                             for c in bidi.call('browsingContext.getTree')['contexts']):
                    time.sleep(0.2)
                context = bidi.call('browsingContext.create', type='tab')['context']

                def go(url):
                    bidi.call('browsingContext.navigate', context=context, url=url, wait='complete')
                    time.sleep(2)  # content script reports the page after 1.5 s

                def js(code):
                    bidi.call('script.evaluate', expression=code, target={'context': context}, awaitPromise=False)
                    time.sleep(0.5)
                base = f'http://127.0.0.1:{port}'
                go(base + '/upload?token=SECRET-TOKEN#access_token=HASH-TOKEN')
                js("cap.value = 'TYPED-SECRET'; ed.innerText = 'EDITOR-SECRET'; ed.click()")
                js('go.click()')
                go(base + '/login')
                js("pw.value = 'PASSWORD-SECRET'; document.getElementById('in').click()")
                go(base + '/start')
                js("em.value = 'EMAIL-SECRET@example.com'; next.click()")
                go(base + '/media/eyJhbGciOiJIUzI1NiJ9PATHTOKEN')
                go(f'http://localhost:{port}/upload')  # not a listed site
                js("go.click()")
                # Automation may not navigate to extension pages; use the start tab the extension opened itself.
                context = next(c['context'] for c in bidi.call('browsingContext.getTree')['contexts']
                               if c['url'].startswith('moz-extension://'))
                js("location.href = 'review.html'")
                time.sleep(1.5)
                saved = bidi.call('script.evaluate', target={'context': context}, awaitPromise=True, expression=
                                  "document.getElementById('save').onclick().then(() => 'ok', e => String(e))")
                self.assertEqual(saved['result'].get('value'), 'ok')
                deadline = time.monotonic() + 15
                while not list(downloads.glob('*.zip')) and time.monotonic() < deadline:
                    time.sleep(0.5)
                time.sleep(1)
                with zipfile.ZipFile(next(downloads.glob('*.zip'))) as zf:
                    self.assertIsNone(zf.testzip())
                    files = {name: zf.read(name) for name in zf.namelist()}
            finally:
                bidi.sock.close()
                process.kill()
                process.wait()
                server.shutdown()
                server.server_close()
            events = [json.loads(line) for line in files['events.jsonl'].decode().splitlines()]
            everything = files['events.jsonl'].decode() + files['network.jsonl'].decode()
            clicks = {e['target']['label']: e for e in events if e['kind'] == 'click'}
            self.assertIn('Schedule post', clicks)
            self.assertIn('Caption', [c['label'] for c in clicks['Schedule post']['controls']])
            self.assertTrue(files['shots/' + clicks['Schedule post']['shot']].startswith(b'\x89PNG'))
            self.assertNotIn('shot', clicks['Sign in'])  # password screen: no screenshot
            self.assertNotIn('shot', clicks['Continue'])  # email-first sign-in step: no screenshot
            self.assertFalse([e for e in events if 'localhost' in e['url']])  # unlisted site: nothing
            for secret in ('TYPED-SECRET', 'EDITOR-SECRET', 'PASSWORD-SECRET', 'SECRET-TOKEN', 'HASH-TOKEN',
                           'EMAIL-SECRET', 'PATHTOKEN'):
                self.assertNotIn(secret, everything)


if __name__ == '__main__':
    unittest.main()
