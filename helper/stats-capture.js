// Keeps a local copy of the stats and earnings responses Fansly's own pages load while the creator browses.
// Read-only: no extra requests are made, the response reaches the page unchanged, and nothing leaves this computer.
// Messages, fan lists and everything else are ignored.
const STATS_URLS = ['*://apiv3.fansly.com/api/v1/*'];
const STATS_PATH = /\/(stats|statistics|earnings)/i;
const KEEP_PER_PATH = 1000;

// Long numeric IDs become {id}, so the same report from different days groups together.
const statsKey = url => new URL(url).pathname.replace(/\/\d{10,}(?=\/|$)/g, '/{id}');

async function saveSnapshot(path, data) {
  const recent = await stats('readonly', store => store.index('path').getAll(path));
  const last = recent[recent.length - 1];
  if (last && JSON.stringify(last.data) === JSON.stringify(data)) return;  // unchanged since last visit
  await stats('readwrite', store => store.add({path, time: Date.now(), data}));
  if (recent.length >= KEEP_PER_PATH) {
    const keys = await stats('readonly', store => store.index('path').getAllKeys(path));
    await stats('readwrite', store => store.delete(keys[0]));
  }
}

browser.webRequest.onBeforeRequest.addListener(details => {
  const path = new URL(details.url).pathname;
  if (details.method !== 'GET' || !STATS_PATH.test(path) || /\/messag/i.test(path)) return;  // never DMs
  const filter = browser.webRequest.filterResponseData(details.requestId);
  const chunks = [];
  filter.ondata = event => {
    chunks.push(event.data);
    filter.write(event.data);  // pass every byte straight through to the page
  };
  filter.onstop = async () => {
    filter.close();
    try {
      saveSnapshot(statsKey(details.url), JSON.parse(await new Blob(chunks).text()));
    } catch (e) { /* not JSON: ignore */ }
  };
  filter.onerror = () => filter.disconnect();
}, {urls: STATS_URLS, types: ['xmlhttprequest']}, ['blocking']);
