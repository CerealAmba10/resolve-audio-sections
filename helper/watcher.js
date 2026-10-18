// Watches the creator's own Fansly posts while they browse Fansly: it reads the replies Fansly's own pages
// already load (no extra requests, the page gets every byte unchanged) and hands the numbers to Ducky's Clip
// Organizer on this computer. Messages are never read. Nothing is kept here except field names for the
// "data shapes" export, the creator's account ID and the IDs of posts Post Helper created.
const WATCH_URLS = ['*://apiv3.fansly.com/api/v1/*'];
const WATCH_PATH = /\/(timeline|post|stats|account\/me)/i;
// The reply each site sends when a post is created: which platform, its new ID and (if the site says) its link.
const CREATES = [
  {platform: 'Fansly', test: /\/api\/v1\/post\/?$/, id: json => json.response && json.response.id},
  {platform: 'RedGifs', test: /\/v2\/gifs\/submit\/?$/, id: json => json.id,
   url: (id) => `https://www.redgifs.com/watch/${String(id).toLowerCase()}`},
  {platform: 'Pornhub Shorts', test: /\/uploading\/save_videodata\/?$/, id: json => json.id, url: (id, json) => json.videoUrl},
];
const CREATE_URLS = [...WATCH_URLS, '*://*.redgifs.com/*', '*://pornhub.mainhub.com/*'];
const NOT_COUNTERS = /^(id|type|version|status|price)$|Id$|At$|Ids$/;
const waiting = {};  // tab id -> clips attached on that tab, waiting for the posts they become (in order)

function readJson(details, handle) {
  const filter = browser.webRequest.filterResponseData(details.requestId);
  const chunks = [];
  filter.ondata = event => {
    chunks.push(event.data);
    filter.write(event.data);  // pass every byte straight through to the page
  };
  filter.onstop = async () => {
    filter.close();
    try {
      await handle(JSON.parse(await new Blob(chunks).text()));
    } catch (e) { /* not JSON, or the app is closed: the numbers are read again next visit */ }
  };
  filter.onerror = () => filter.disconnect();
}

function* objects(value) {
  if (Array.isArray(value)) for (const v of value) yield* objects(v);
  else if (value && typeof value === 'object') {
    yield value;
    for (const v of Object.values(value)) yield* objects(v);
  }
}

// A post looks like {id, accountId, createdAt, ...numbers}; keep the creator's own (or ones Post Helper created).
function ownPosts(json, me, created) {
  const found = new Map();
  for (const o of objects(json)) {
    if (o.id == null || o.accountId == null || !('createdAt' in o)) continue;
    if (String(o.accountId) !== me && !created.includes(String(o.id))) continue;
    const counters = {};
    for (const [k, v] of Object.entries(o)) if (typeof v === 'number' && !NOT_COUNTERS.test(k)) counters[k] = v;
    const at = typeof o.createdAt === 'number' ? new Date(o.createdAt < 1e12 ? o.createdAt * 1000 : o.createdAt) : null;
    found.set(String(o.id), {id: String(o.id), counters,
      created: at && !isNaN(at) ? new Date(at - at.getTimezoneOffset() * 60000).toISOString().slice(0, 16).replace('T', ' ') : null});
  }
  return [...found.values()];
}

// Field names and value types only, for the "Export data shapes" button.
function shape(value) {
  if (Array.isArray(value)) return value.length ? [shape(value[0])] : [];
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[/^\d+$/.test(k) ? '{id}' : k] = shape(v);
    return out;
  }
  return value === null ? 'null' : typeof value;
}

// A new post on any of the sites: link it (and its address) to the clip Post Helper attached on that tab.
browser.webRequest.onBeforeRequest.addListener(details => {
  const path = new URL(details.url).pathname;
  const site = details.method === 'POST' && CREATES.find(c => c.test.test(path));
  if (!site || !waiting[details.tabId] || !waiting[details.tabId].length) return;
  readJson(details, async json => {
    const id = json && site.id(json);
    const clip = (waiting[details.tabId] || []).shift();
    if (id == null || !clip) return;
    await inTurn(async () => {
      const {created = []} = await browser.storage.local.get('created');
      await browser.storage.local.set({created: [...created, String(id)].slice(-2000)});
    });
    await queue({path: '/api/link', body: {platform: site.platform, post_id: String(id), fp: clip.fp,
                                           url: site.url ? site.url(id, json) || null : null}});
  });
}, {urls: CREATE_URLS, types: ['xmlhttprequest']}, ['blocking']);

browser.webRequest.onBeforeRequest.addListener(details => {
  const path = new URL(details.url).pathname;
  if (/\/messag|\/group/i.test(path)) return;  // never messages
  if (details.method === 'POST') return;  // new posts are handled above
  if (details.method !== 'GET' || !WATCH_PATH.test(path)) return;
  readJson(details, async json => {
    const key = path.replace(/\/\d{6,}(?=\/|$)/g, '/{id}');
    const {shapes = {}, me = null, created = []} = await browser.storage.local.get(['shapes', 'me', 'created']);
    shapes[key] = shape(json);
    let account = me;
    if (/\/account\/me\/?$/.test(path)) {
      const r = json && json.response;
      const id = r && ((r.account && r.account.id) || r.id);
      if (id != null) account = String(id);
    }
    await browser.storage.local.set({shapes, me: account});
    const posts = ownPosts(json, account, created);
    if (posts.length) await app('/api/watch', {platform: 'Fansly', posts});
  });
}, {urls: WATCH_URLS, types: ['xmlhttprequest']}, ['blocking']);
