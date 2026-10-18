// Recording lives in memory only; review.html reads it and saves the zip the person chooses to send.
var steps = []; // var, not const: review.html reads these through getBackgroundPage()
var network = [];
var watching = false;  // nothing is recorded until the person clicks Start watching
var shapes = {};  // "POST /api/v1/post" -> {request, response}: field names and value types only, never values
// Any sign-in step (email, password, one-time code) gets no screenshot.
const SIGN_IN = /log-?in|sign-?in|auth|2fa|mfa|verif|password|passkey|recover|security/i;
const STATIC = /\.(js|css|png|jpe?g|gif|webp|svg|woff2?|ttf|ico|mp4|m3u8|ts|webm)$/i;

// Host and path only; query, fragment and token-like path parts can carry credentials.
function cleanUrl(raw) {
  try {
    const url = new URL(raw);
    const path = url.pathname.split('/').map(s => s.length >= 20 && !/^\d+$/.test(s) ? '{redacted}' : s).join('/');
    return url.protocol + '//' + url.host + path;
  } catch (e) {
    return '';
  }
}

function badge() {
  browser.browserAction.setBadgeBackgroundColor({color: watching ? '#e5624b' : '#6b655d'});
  browser.browserAction.setBadgeText({text: watching ? String(steps.length || 'REC') : steps.length ? String(steps.length) : ''});
}

browser.runtime.onMessage.addListener(async (data, sender) => {
  if (data.type === 'state') return {watching, steps: steps.length};
  if (data.type === 'watch') {
    watching = !!data.on;
    badge();
    return {watching};
  }
  if (!watching || !sender.tab || !allowed(sender.tab.url)) return;
  data.url = cleanUrl(data.url);
  data.n = steps.length + 1;
  data.time = Math.round(Date.now() / 100) / 10;
  if (SIGN_IN.test(data.url + ' ' + data.title + ' ' + sender.tab.url)) data.sensitive = true;
  steps.push(data);
  badge();
  // Capture now, matching the controls just checked for sign-in fields; later states get their own steps.
  if (!data.sensitive && sender.tab.active) {
    try {
      data.shot = await browser.tabs.captureVisibleTab(sender.tab.windowId, {format: 'png'});
    } catch (e) { /* window minimised or tab changed */ }
  }
});

browser.webRequest.onCompleted.addListener(d => {
  if (!watching || !allowed(d.documentUrl || d.originUrl || d.url)) return;
  if (d.method === 'GET' && (!['xmlhttprequest', 'main_frame'].includes(d.type) || STATIC.test(new URL(d.url).pathname))) return;
  network.push({time: Math.round(d.timeStamp / 100) / 10, method: d.method, status: d.statusCode, type: d.type, url: cleanUrl(d.url)});
}, {urls: ['<all_urls>']});

// Field names and value types only ("price": "number"), never the values themselves.
function shape(value) {
  if (Array.isArray(value)) return value.length ? [value.map(shape).reduce(merge)] : [];
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[/^\d+$/.test(k) ? '{id}' : k] = shape(v);
    return out;
  }
  return value === null ? 'null' : typeof value;
}

// Combine two shapes so fields seen only sometimes are kept.
function merge(a, b) {
  if (Array.isArray(a) && Array.isArray(b)) return a.length && b.length ? [merge(a[0], b[0])] : a.length ? a : b;
  if (a && b && typeof a === 'object' && typeof b === 'object' && !Array.isArray(a) && !Array.isArray(b)) {
    const out = {...a};
    for (const [k, v] of Object.entries(b)) out[k] = k in a ? merge(a[k], v) : v;
    return out;
  }
  return a === b || b === 'null' ? a : a === 'null' ? b : `${a}|${b}`;
}

function keep(key, part, json) {
  const entry = shapes[key] = shapes[key] || {};
  const s = shape(json);
  entry[part] = entry[part] ? merge(entry[part], s) : s;
}

// What the site's own pages send and receive, as shapes. Never messages, sign-in or payment calls.
browser.webRequest.onBeforeRequest.addListener(d => {
  const url = new URL(d.url);
  if (!watching || !allowed(d.documentUrl || d.originUrl || d.url) || STATIC.test(url.pathname) ||
      /messag|chat|group|wallet|payment|card/i.test(url.pathname) || SIGN_IN.test(url.pathname)) return;
  const key = `${d.method} ${cleanUrl(d.url).replace(/^https?:\/\/[^/]+/, '').replace(/\/\d{6,}(?=\/|$)/g, '/{id}')}`;
  const body = d.requestBody;
  if (body && body.raw && body.raw[0] && body.raw[0].bytes) {
    try { keep(key, 'request', JSON.parse(new TextDecoder().decode(body.raw[0].bytes))); } catch (e) { /* not JSON */ }
  } else if (body && body.formData) {
    keep(key, 'request', Object.fromEntries(Object.keys(body.formData).map(k => [k, 'string'])));
  }
  if (d.type !== 'xmlhttprequest') return;
  const filter = browser.webRequest.filterResponseData(d.requestId);
  const chunks = [];
  filter.ondata = e => { chunks.push(e.data); filter.write(e.data); };  // the page gets every byte unchanged
  filter.onstop = async () => {
    filter.close();
    try { keep(key, 'response', JSON.parse(await new Blob(chunks).text())); } catch (e) { /* not JSON */ }
  };
  filter.onerror = () => filter.disconnect();
}, {urls: SITE_URLS}, ['blocking', 'requestBody']);

browser.runtime.onInstalled.addListener(() => browser.tabs.create({url: 'start.html'}));
