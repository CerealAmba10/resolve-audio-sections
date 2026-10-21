import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import organizer


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name, 'clips')
        self.folder.mkdir()
        for n, name in enumerate(['one.mp4', 'two.mp4', 'three.mp4']):
            (self.folder / name).write_bytes(f'clip {n}'.encode() * 50)
        self.app = organizer.App(Path(self.temp.name, 'data'), probe=lambda p: dict(duration=30, width=1080, height=1920))
        self.server = organizer.serve(self.app, port=0)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.app.db.close()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        default = {'Host': f'127.0.0.1:{self.port}', organizer.HEADER: '1', 'Content-Type': 'application/json'}
        conn.request(method, path, json.dumps(body) if body is not None else None, {**default, **(headers or {})})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        kind = response.getheader('Content-Type', '')
        return response.status, json.loads(data) if data and kind.startswith('application/json') else data

    def test_refuses_other_sites_and_hosts(self):
        self.assertEqual(self.call('GET', '/api/state', headers={organizer.HEADER: ''})[0], 403)
        self.assertEqual(self.call('POST', '/api/quit', {}, headers={organizer.HEADER: ''})[0], 403)
        self.assertEqual(self.call('GET', '/api/state', headers={'Host': 'evil.example:80'})[0], 403)
        self.assertEqual(self.call('GET', '/', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.call('GET', '/')[0], 200)
        status, body = self.call('POST', '/api/open', {'path': 'C:\\Windows'})
        self.assertEqual((status, body['error']), (400, 'Only library and batch folders can be opened.'))

    def test_folder_rename_batch_pick_and_post(self):
        folder = self.call('POST', '/api/folders', {'path': str(self.folder)})[1]['path']
        self.assertEqual(self.call('GET', '/api/state')[1]['folders'][0]['fresh'], 3)
        plan = self.call('POST', '/api/rename', {'folder': folder, 'prefix': 'SFW'})[1]['plan']
        self.assertEqual(len(plan), 3)
        self.assertFalse((self.folder / 'SFW 1.mp4').exists())  # preview changes nothing
        self.call('POST', '/api/rename', {'folder': folder, 'prefix': 'SFW', 'apply': True})
        self.assertTrue((self.folder / 'SFW 3.mp4').exists())
        chosen = self.call('POST', '/api/batch/preview', {'folder': folder, 'count': 2, 'seed': 1})[1]['chosen']
        self.assertEqual([c['reason'] for c in chosen], ['never posted', 'never posted'])
        created = self.call('POST', '/api/batch', {'folder': folder, 'names': [c['name'] for c in chosen],
                                                   'platforms': ['Fansly', 'Instagram']})[1]
        self.assertEqual((created['copied'], created['planned']), (2, 2))  # Instagram is never planned
        self.assertTrue((Path(created['path']) / 'batch.json').exists())
        picker = self.call('GET', '/api/helper/clips?platform=Fansly')[1]
        self.assertEqual(len(picker['planned']), 2)
        self.assertEqual(picker['batches'][0]['name'], created['name'])
        self.assertEqual([i['caption'] for i in picker['batches'][0]['items']], [p['caption'] for p in picker['planned']])
        first = picker['planned'][0]
        self.assertEqual(self.call('GET', '/file/' + first['fp'])[1], (self.folder / first['name']).read_bytes())
        self.assertTrue(self.call('POST', '/api/posted', {'platform': 'Fansly', 'fp': first['fp'],
                                                          'source': 'post-helper'})[1]['ok'])
        self.assertEqual(len(self.call('GET', '/api/helper/clips?platform=Fansly')[1]['planned']), 1)  # plan closed
        state = self.call('GET', '/api/state')[1]
        self.assertTrue(state['helper'])
        detail = self.call('GET', '/api/clip/' + first['fp'])[1]
        self.assertEqual([p['platform'] for p in detail['posts']], ['Fansly'])
        self.assertEqual(sum(w['platforms'].get('Fansly', 0) for w in self.call('GET', '/api/activity')[1]['weeks']), 1)
        self.assertEqual(self.call('GET', '/file/' + first['fp'], headers={organizer.HEADER: ''})[0], 403)

    def test_presets_per_site_and_batches_use_them(self):
        presets = self.call('GET', '/api/presets')[1]['presets']
        self.assertEqual({p['platform'] for p in presets if p['name'] == 'Default'},
                         {'Fansly', 'RedGifs', 'Pornhub Shorts', 'Instagram', 'TikTok', 'Reddit'})  # one each to start
        status, vtuber = self.call('POST', '/api/presets', {'platform': 'Pornhub Shorts', 'name': 'Vtuber', 'values': {
            'tags': 'vtuber, cosplay', 'categories': 'Anime, Cosplay', 'who': 'Solo/Animation', 'clip': 'any',
            'max_seconds': '0', 'bogus': 1}})
        self.assertEqual(status, 200)
        self.assertEqual((vtuber['settings']['tags'], vtuber['settings']['max_seconds']), ('vtuber, cosplay', 0))
        self.assertNotIn('bogus', vtuber['settings'])
        self.assertEqual(self.call('POST', '/api/presets', {'platform': 'Pornhub Shorts', 'name': 'Vtuber', 'values': {}})[0], 400)
        self.assertEqual(self.call('POST', '/api/presets', {'platform': 'RedGifs', 'name': 'Bad', 'values': {'times': 'noon'}})[0], 400)
        folder = self.call('POST', '/api/folders', {'path': str(self.folder)})[1]['path']
        created = self.call('POST', '/api/batch', {'folder': folder, 'names': ['one.mp4'], 'platforms': ['Pornhub Shorts'],
                                                   'presets': {'Pornhub Shorts': vtuber['id']}})[1]
        self.assertEqual(created['planned'], 1)
        planned = self.call('GET', '/api/helper/clips?platform=Pornhub Shorts'.replace(' ', '%20'))[1]
        self.assertEqual((planned['planned'][0]['options']['preset'], planned['planned'][0]['options']['tags']),
                         ('Vtuber', 'vtuber, cosplay'))
        self.assertEqual([p['name'] for p in planned['presets']], ['Default', 'Vtuber'])
        self.assertEqual(self.call('POST', '/api/tags', {'platform': 'RedGifs', 'kind': 'tags', 'names': ['Anime', 'POV']})[1],
                         {'saved': 2})
        self.assertEqual(self.call('GET', '/api/presets')[1]['tags']['RedGifs'], {'tags': ['Anime', 'POV']})
        self.assertEqual(self.call('POST', '/api/presets/delete', {'id': vtuber['id']})[0], 200)
        default = next(p for p in self.call('GET', '/api/presets')[1]['presets'] if p['platform'] == 'Pornhub Shorts')
        self.assertEqual(self.call('POST', '/api/presets/delete', {'id': default['id']})[0], 400)  # keep one per site


class SingleCopyTests(unittest.TestCase):
    def test_second_copy_cannot_share_the_port(self):
        with tempfile.TemporaryDirectory() as home:
            first, second = organizer.App(Path(home, 'a')), organizer.App(Path(home, 'b'))
            server = organizer.serve(first, port=0)
            try:
                with self.assertRaises(OSError):  # so a second launch shows the running window instead
                    organizer.serve(second, port=server.server_port)
            finally:
                server.server_close()
                first.db.close()
                second.db.close()


class UpdateTests(unittest.TestCase):
    def test_release_check_and_checksum_guard(self):
        import hashlib
        import io
        import sys
        from unittest.mock import MagicMock, patch
        new_exe = b'NEW-BUILD'
        ours = {'tag_name': 'organizer-v9.0.0', 'body': 'Notes', 'assets': [{
            'name': organizer.ASSET, 'browser_download_url': 'https://example.invalid/ClipOrganizer.exe',
            'digest': 'sha256:' + hashlib.sha256(new_exe).hexdigest()}]}
        # The same repo also publishes Audio Sections releases (plain vX.Y, newer and without our exe): skip those.
        release_json = [{'tag_name': 'v10.0', 'assets': [{'name': 'AudioSectionsLite-Windows.zip'}]},
                        {'tag_name': 'organizer-v2.0.0', 'draft': True, 'assets': ours['assets']}, ours,
                        {'tag_name': 'organizer-v1.5.0', 'assets': ours['assets']}]

        def fake_urlopen(request, timeout=0):
            body = json.dumps(release_json).encode() if 'api.github.com' in request.full_url else new_exe
            response = io.BytesIO(body)
            response.__enter__ = lambda s=response: s
            response.__exit__ = lambda *a: None
            return response
        with tempfile.TemporaryDirectory() as folder, patch('urllib.request.urlopen', fake_urlopen), \
                patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(Path(folder, 'app.exe'))), \
                patch('organizer.threading.Thread', MagicMock()):
            exe = Path(folder, 'app.exe')
            exe.write_bytes(b'OLD-BUILD')
            release = organizer.latest_release()
            self.assertTrue(release['available'])
            self.assertEqual(release['version'], '9.0.0')
            bad = dict(release, asset=dict(release['asset'], digest='sha256:' + '0' * 64))
            with self.assertRaises(ValueError):
                organizer.install_update(bad, None)
            self.assertEqual(exe.read_bytes(), b'OLD-BUILD')  # wrong checksum: nothing replaced
            with self.assertRaises(ValueError):
                organizer.install_update(dict(release, asset=dict(release['asset'], digest=None)), None)
            organizer.install_update(release, None)
            self.assertEqual(exe.read_bytes(), b'NEW-BUILD')
            self.assertEqual(exe.with_suffix('.old').read_bytes(), b'OLD-BUILD')
        self.assertFalse(organizer.version_tuple('1.0.0') > organizer.version_tuple(organizer.VERSION))


if __name__ == '__main__':
    unittest.main()
