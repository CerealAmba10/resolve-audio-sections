import http.server
import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import itertools
from unittest import mock

import library
import organizer
from scheduler import DEFAULTS
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


# Stand-ins for the real upload pages, rebuilt from the recordings: same field names, placeholders and labels.
REDGIFS = b"""<button id="pickvid" type="button">select a video</button><input type="file" id="vid" accept="video/*" style="display:none">
<ul><li><label>Straight<input type="radio" name="straight" checked></label></li><li><label>Gay<input type="radio" name="gay"></label></li>
<li><label>Animated<input type="radio" name="animated"></label></li></ul>
<div><div><input placeholder="Type to search for tags..." id="tagq"></div><div id="tagopts"></div></div><div id="picked"></div>
<div><div><input placeholder="Search..." id="nicheq"></div></div>
<div id="niches"><label><input type="checkbox" name="rough-sex"></label><label><input type="checkbox" name="hanime"></label></div>
<textarea placeholder="Give your content more context: describe what's happening"></textarea>
<div><span>Make Private</span><div><label><input type="checkbox" id="priv"></label></div></div>
<button id="publish" type="button">publish</button>
<script>
pickvid.onclick = () => vid.click();
vid.onchange = () => document.title = 'got ' + [...vid.files].map(f => f.name).join(',');
const TAGS = ['Anime', 'Animation', 'Hentai', 'ASMR', 'POV'];
tagq.oninput = () => {
  tagopts.innerHTML = '';
  for (const t of TAGS.filter(t => tagq.value && t.toLowerCase().startsWith(tagq.value.toLowerCase()))) {
    const b = document.createElement('button');
    b.setAttribute('aria-label', 'Select ' + t + ' tag');
    b.textContent = t;
    b.onclick = () => { picked.textContent += (picked.textContent ? ',' : '') + t; };
    tagopts.append(b);
  }
};
nicheq.oninput = () => {
  if (/cos/i.test(nicheq.value) && !document.querySelector('[name=cosplay]')) {
    const l = document.createElement('label'); l.innerHTML = '<input type="checkbox" name="cosplay">'; niches.append(l);
  }
};
publish.onclick = () => { window.published = true; fetch('/v2/gifs/submit', {method: 'POST', body: '{}'}); };
fetch('/v1/tags'); fetch('/v2/niches/suggest/adding');
</script>"""
PORNHUB = b"""<button id="sel" type="button">Select Videos to Upload</button>
<input type="file" class="dz-hidden-input" id="vid" multiple accept="video/*" style="display:none">
<div id="queue"></div><button id="submit" type="button">Submit for Review</button>
<script>
const TAGS = ['Amateur', 'Vtuber', 'Vtuber Asmr', 'Anime'], CATS = ['Amateur', 'Cumshot', 'Anime', 'Cosplay'];
let n = 0;
sel.onclick = () => vid.click();
vid.onchange = () => {
  document.title = 'got ' + [...vid.files].map(f => f.name).join(',');
  for (const f of vid.files) queue.append(item());
};
function suggest(input, list, values, cls, pills) {
  input.oninput = () => {
    list.innerHTML = '';
    for (const v of values.filter(v => input.value && v.toLowerCase().startsWith(input.value.toLowerCase()))) {
      const li = document.createElement('li'); li.className = cls; li.innerHTML = '<span>' + v + '</span>';
      li.onclick = () => { pills.insertAdjacentHTML('beforeend', '<div class="c-pill">' + v + '</div>'); list.innerHTML = ''; };
      list.append(li);
    }
  };
}
function item() {
  const d = document.createElement('div'); d.className = 'item'; n += 1;
  d.innerHTML = '<div class="performerRadioGroup"><label><input type="radio" name="who' + n + '">Performer(s)</label>' +
    '<label><input type="radio" class="solo" name="who' + n + '">Solo/Animation</label></div><input name="title">' +
    '<div><input name="tags"><ul class="tagopts"></ul></div><div class="tagpills"></div>' +
    '<div><input name="category"><ul class="catopts"></ul></div><div class="catpills"></div>' +
    '<v-switch><div><div><label class="c-input-label noMargin">Publish to Shorties Only</label><div>' +
    '<input type="checkbox" class="shortie" id="short' + n + '" style="display:none">' +
    '<label class="c-switch__slider" for="short' + n + '"></label></div></div></div></v-switch>' +
    '<label><input type="checkbox" class="certify">SELECT ALL AND CERTIFY.</label>';
  const tags = d.querySelector('[name=tags]');
  suggest(tags, d.querySelector('.tagopts'), TAGS, '', d.querySelector('.tagpills'));
  tags.onkeydown = e => { if (e.key === 'Enter' && tags.value) {
    d.querySelector('.tagpills').insertAdjacentHTML('beforeend', '<div class="c-pill">' + tags.value + '</div>'); tags.value = ''; } };
  suggest(d.querySelector('[name=category]'), d.querySelector('.catopts'), CATS, 'autoComplete_item', d.querySelector('.catpills'));
  fetch('/video-manager/abc123', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: JSON.stringify({type: 'category', partialString: 'a', videoType: 'video'})});
  return d;
}
submit.onclick = async () => { for (const it of document.querySelectorAll('.item')) await fetch('/uploading/save_videodata', {method: 'POST', body: 'x'}); };
</script>"""
PH_IDS = itertools.count(123)
SETTINGS = {'RedGifs': dict(tags='Anime, Hentai, NotARealTag', niches='Rough Sex, Cosplay', audience='Animated', private=True),
            'Pornhub Shorts': dict(tags='Vtuber, my custom tag', categories='Anime, Cosplay, NotACategory', who='Solo/Animation',
                                   shorties=True)}


def fake_settings(platforms):
    return {p: {**DEFAULTS[p], 'enabled': True, **SETTINGS.get(p, {})} for p in platforms if p in DEFAULTS}


class Site(http.server.BaseHTTPRequestHandler):
    def send_body(self, body, kind='application/json'):
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

    def do_GET(self):
        if self.path == '/v1/tags':
            return self.send_body({'tags': [{'name': 'Anime', 'count': 9}, {'name': 'Hentai', 'count': 7}, {'name': 'POV', 'count': 3}]})
        if self.path == '/v2/niches/suggest/adding':
            return self.send_body({'niches': [{'id': 'rough-sex', 'name': 'Rough Sex'}, {'id': 'cosplay', 'name': 'Cosplay'}]})
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
        page = {'/profile': PROFILE, '/redgifs': REDGIFS, '/pornhub': PORNHUB}.get(self.path, FORM)
        self.send_body(b'<!doctype html><body>' + page, 'text/html')

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length') or 0))
        if self.path.startswith('/video-manager/'):
            return self.send_body({'Anime': 12, 'Cosplay': 4, 'Amateur': 30})
        if self.path == '/v2/gifs/submit':
            return self.send_body({'id': 'CheerfulRedPanda', 'duration': 12})
        if self.path == '/uploading/save_videodata':
            n = next(PH_IDS)
            return self.send_body({'success': True, 'message': 'ok', 'id': n,
                                   'videoUrl': f'https://www.pornhub.com/view_video.php?viewkey=ph{n}'})
        self.send_body({'success': True, 'response': {'id': '900', 'accountId': '412345678901234567'}})

    def log_message(self, *args):
        pass


@unittest.skipIf(not FIREFOX, 'Firefox not installed')
class PostHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        settings = mock.patch.object(library, 'platform_settings', fake_settings)
        settings.start()
        self.addCleanup(settings.stop)
        # Which site the local test server stands in for in this test.
        self.platform = 'RedGifs' if 'redgifs' in self._testMethodName else \
            'Pornhub Shorts' if 'pornhub' in self._testMethodName else 'Fansly'
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
        hosts = {'Fansly': ['fansly.com'], 'RedGifs': ['redgifs.com'], 'Pornhub Shorts': ['pornhub.mainhub.com']}
        hosts[self.platform] = ['127.0.0.1']
        sites = (ext / 'sites.js').read_text(encoding='utf-8').splitlines()
        (ext / 'sites.js').write_text('\n'.join(f'const HOSTS = {json.dumps(hosts)};' if line.startswith('const HOSTS')
                                                else line for line in sites), encoding='utf-8')
        for name, old, new in (
                               ('store.js', 'http://127.0.0.1:47833', f'http://127.0.0.1:{self.servers[0].server_port}'),
                               ('watcher.js', "['*://apiv3.fansly.com/api/v1/*']", "['http://127.0.0.1/api/v1/*']"),
                               ('watcher.js', "'*://*.redgifs.com/*', '*://pornhub.mainhub.com/*'", "'http://127.0.0.1/*'"),
                               ('watcher.js', "'*://api.redgifs.com/*', '*://pornhub.mainhub.com/*'", "'http://127.0.0.1/*'")):
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

    def attach_from_picker(self, page, path, opener, choose):
        self.bidi.call('browsingContext.navigate', context=page, url=self.site + path, wait='complete')
        time.sleep(0.5)
        self.js(page, opener)  # the site's own upload button
        picker = self.context('/picker.html')
        self.wait(picker, "document.querySelectorAll('.tile').length >= 2")
        self.js(picker, choose)
        return self.wait(page, "document.title.startsWith('got') && document.title")

    def fill(self, page):
        card = "document.getElementById('post-helper-card').shadowRoot"
        return self.wait(page, f"{card}?.querySelector('.note')?.textContent.startsWith('Auto-filled') && "
                               f"{card}.querySelector('.note').textContent", timeout=40)

    def fill_again(self, page):
        card = "document.getElementById('post-helper-card').shadowRoot"
        self.js(page, f"{card}.querySelector('[data-act=fill]').click()")
        time.sleep(0.5)
        return self.wait(page, f"!{card}.querySelector('[data-act=fill]')?.disabled && {card}.querySelector('.note').textContent",
                         timeout=40)

    def links(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = self.app.db.execute('SELECT s.platform, s.post_id, s.url, c.name FROM site_posts s JOIN clips c ON c.fp=s.fp '
                                       'ORDER BY s.post_id').fetchall()
            if rows:
                return [tuple(r) for r in rows]
            time.sleep(0.3)
        return []

    def test_redgifs_fill_each_step_never_publishes(self):
        page = self.bidi.call('browsingContext.create', type='tab')['context']
        got = self.attach_from_picker(page, '/redgifs', 'pickvid.click()',
                                      "[...document.querySelectorAll('.tile')].pop().click(); document.getElementById('attach').click()")
        self.assertTrue(got.startswith('got '))
        note = self.fill(page)
        self.assertTrue(self.js(page, "document.querySelector('[name=animated]').checked"))
        self.assertEqual(self.js(page, 'picked.textContent'), 'Anime,Hentai')
        self.assertIn('RedGifs has no tag called: NotARealTag', note)
        self.assertTrue(self.js(page, "document.querySelector('[name=rough-sex]').checked"))
        self.assertTrue(self.js(page, "document.querySelector('[name=cosplay]').checked"))  # found by searching
        self.assertTrue(self.js(page, 'priv.checked'))
        self.fill_again(page)
        self.assertEqual(self.js(page, 'picked.textContent'), 'Anime,Hentai')  # filling again adds nothing twice
        self.assertFalse(self.js(page, '!!window.published'))  # Publish stays the creator's click
        learned = self.app.get('/api/presets', {})['tags']['RedGifs']
        self.assertEqual((learned['tags'], learned['niches']), (['Anime', 'Hentai', 'POV'], ['Cosplay', 'Rough Sex']))
        self.js(page, 'publish.click()')
        rows = self.links()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:3], ('RedGifs', 'CheerfulRedPanda', 'https://www.redgifs.com/watch/cheerfulredpanda'))

    def test_pornhub_fill_queue_leaves_certification_to_creator(self):
        page = self.bidi.call('browsingContext.create', type='tab')['context']
        self.attach_from_picker(page, '/pornhub', 'sel.click()',
                                "[...document.querySelectorAll('h2 button')].find(b => b.textContent === 'Attach all 2').click()")
        note = self.fill(page)
        items = "[...document.querySelectorAll('.item')]"
        self.assertEqual(self.js(page, f"{items}.map(i => i.querySelector('[name=title]').value).join('|')"), 'SFW 1|SFW 2')
        self.assertEqual(self.js(page, f"{items}.map(i => [...i.querySelectorAll('.tagpills .c-pill')].map(p => p.textContent).join(',')).join('|')"),
                         'Vtuber,my custom tag|Vtuber,my custom tag')  # picked from the list, and typed
        self.assertEqual(self.js(page, f"{items}.map(i => [...i.querySelectorAll('.catpills .c-pill')].map(p => p.textContent).join(',')).join('|')"),
                         'Anime,Cosplay|Anime,Cosplay')
        self.assertIn("Not in Pornhub's category list: NotACategory", note)
        self.assertTrue(self.js(page, f"{items}.every(i => i.querySelector('.solo').checked)"))
        self.assertTrue(self.js(page, f"{items}.every(i => i.querySelector('.shortie').checked)"))  # Shorties switch on
        self.assertIn('Shorties on', note)
        self.fill_again(page)
        self.assertEqual(self.js(page, f"{items}.map(i => i.querySelectorAll('.tagpills .c-pill').length).join()"), '2,2')  # no doubles
        self.assertEqual(self.app.get('/api/presets', {})['tags']['Pornhub Shorts'], {'categories': ['Amateur', 'Anime', 'Cosplay']})
        self.assertFalse(self.js(page, "[...document.querySelectorAll('.certify')].some(c => c.checked)"))  # never ticked for them
        self.js(page, 'submit.click()')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and len(self.links()) < 2:
            time.sleep(0.3)
        rows = self.links()
        self.assertEqual([(r[0], r[3]) for r in rows], [('Pornhub Shorts', 'SFW 1.mp4'), ('Pornhub Shorts', 'SFW 2.mp4')])
        self.assertTrue(all(r[2] == f'https://www.pornhub.com/view_video.php?viewkey=ph{r[1]}' for r in rows))

    def test_redgifs_picker_preset_choice(self):
        pov = library.save_preset(self.app.db, 'RedGifs', 'POV set', {'tags': 'POV', 'audience': 'Gay', 'niches': ''})
        page = self.bidi.call('browsingContext.create', type='tab')['context']
        self.attach_from_picker(page, '/redgifs', 'pickvid.click()',
                                f"const s = document.getElementById('preset'); s.value = '{pov['id']}'; s.dispatchEvent(new Event('change'));"
                                " [...document.querySelectorAll('.tile')].pop().click(); document.getElementById('attach').click()")
        self.fill(page)
        self.assertTrue(self.js(page, "document.querySelector('[name=gay]').checked"))
        self.assertEqual(self.js(page, 'picked.textContent'), 'POV')
        card = "document.getElementById('post-helper-card').shadowRoot"
        self.assertIn('POV set', self.js(page, f"{card}.textContent"))

if __name__ == '__main__':
    unittest.main()
