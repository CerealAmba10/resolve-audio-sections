// Recording lives in memory only; review.html reads it and saves the zip the person chooses to send.
var steps = []; // var, not const: review.html reads these through getBackgroundPage()
var network = [];
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
  browser.browserAction.setBadgeText({text: steps.length ? String(steps.length) : ''});
}

browser.runtime.onMessage.addListener(async (data, sender) => {
  if (!sender.tab || !allowed(sender.tab.url)) return;
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
  if (!allowed(d.documentUrl || d.originUrl || d.url)) return;
  if (d.method === 'GET' && (!['xmlhttprequest', 'main_frame'].includes(d.type) || STATIC.test(new URL(d.url).pathname))) return;
  network.push({time: Math.round(d.timeStamp / 100) / 10, method: d.method, status: d.statusCode, type: d.type, url: cleanUrl(d.url)});
}, {urls: ['<all_urls>']});

browser.browserAction.onClicked.addListener(() => browser.tabs.create({url: 'review.html'}));
browser.runtime.onInstalled.addListener(() => browser.tabs.create({url: 'start.html'}));
