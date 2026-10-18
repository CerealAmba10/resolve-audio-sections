// Connects the picker and the post card on each site to Ducky’s Clip Organizer on this computer.
// Everything that reads or changes the waiting list runs one at a time, so overlapping posts can't overwrite
// each other's entries and nothing is ever sent twice.
let line = Promise.resolve();
function inTurn(job) {
  const result = line.then(job, job);
  line = result.catch(() => {});
  return result;
}

async function sendPending() {
  const {pending = []} = await browser.storage.local.get('pending');
  const left = [];
  for (const item of pending) {
    try {
      await app(item.path || '/api/posted', item.body || item);
    } catch (e) {
      left.push(item);  // app closed: keep it and try again later
    }
  }
  await browser.storage.local.set({pending: left});
  return left.length;
}

const flushPending = () => inTurn(sendPending);

// Things the app must not miss (posts, post-to-clip links) wait here until it's open.
function queue(...items) {
  return inTurn(async () => {
    const {pending = []} = await browser.storage.local.get('pending');
    await browser.storage.local.set({pending: [...pending, ...items]});
  }).then(flushPending);
}

browser.runtime.onMessage.addListener(async (message, sender) => {
  const tab = sender.tab && sender.tab.id;
  if (message.type === 'picker-data') {
    flushPending();
    return app(`/api/helper/clips?platform=${encodeURIComponent(message.platform)}`);
  }
  if (message.type === 'attach') {
    // The page's content script fetches the clips itself (as file-backed Blobs, so big batches don't fill memory).
    await browser.tabs.sendMessage(tab, {type: 'attach', planned: message.planned || null, clips: message.clips,
                                         bulk: !!message.bulk, options: message.options || {}});
    return true;
  }
  if (message.type === 'expect-posts') {
    waiting[tab] = message.clips.slice();  // the next posts created on this tab become these clips, in order
    const site = platformFor(sender.tab.url);
    if (site) lastTab[site] = tab;
    return true;
  }
  if (message.type === 'close-picker' || message.type === 'browse') {
    await browser.tabs.sendMessage(tab, {type: message.type});
    return true;
  }
  if (message.type === 'posted') {
    const local = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
    const at = local.toISOString().slice(0, 16).replace('T', ' ');
    const waiting = await queue(...message.fps.map(fp =>
      ({path: '/api/posted', body: {platform: message.platform, fp, source: 'post-helper', at}})));
    return {waiting};
  }
  if (message.type === 'status') {
    try {
      await app('/api/state');
      return {connected: true, waiting: await flushPending()};
    } catch (e) {
      return {connected: false, waiting: ((await browser.storage.local.get('pending')).pending || []).length};
    }
  }
});

browser.browserAction.onClicked.addListener(() => browser.tabs.create({url: 'helper.html'}));
browser.runtime.onInstalled.addListener(() => browser.tabs.create({url: 'helper.html'}));
