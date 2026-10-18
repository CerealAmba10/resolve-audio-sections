// Connects the picker and the post card on each site to Clip Organizer on this computer.
async function flushPending() {
  const {pending = []} = await browser.storage.local.get('pending');
  const left = [];
  for (const post of pending) {
    try {
      await app('/api/posted', post);
    } catch (e) {
      left.push(post);  // app closed: keep it and try again later
    }
  }
  await browser.storage.local.set({pending: left});
  return left.length;
}

browser.runtime.onMessage.addListener(async (message, sender) => {
  const tab = sender.tab && sender.tab.id;
  if (message.type === 'picker-data') {
    flushPending();
    return app(`/api/helper/clips?platform=${encodeURIComponent(message.platform)}`);
  }
  if (message.type === 'attach') {
    // Fetch each chosen clip from the app and hand it to the page's own upload field.
    const files = [];
    for (const clip of message.clips) {
      const response = await app('/file/' + clip.fp);
      files.push({name: clip.name, type: guessType(clip.name), bytes: await response.arrayBuffer()});
    }
    await browser.tabs.sendMessage(tab, {type: 'attach', files, planned: message.planned || null, clip: message.clips[0]});
    return true;
  }
  if (message.type === 'close-picker' || message.type === 'browse') {
    await browser.tabs.sendMessage(tab, {type: message.type});
    return true;
  }
  if (message.type === 'posted') {
    const post = {platform: message.platform, fp: message.fp, source: 'post-helper',
                  at: new Date().toISOString().slice(0, 16).replace('T', ' ')};
    const {pending = []} = await browser.storage.local.get('pending');
    await browser.storage.local.set({pending: [...pending, post]});
    return {waiting: await flushPending()};
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

function guessType(name) {
  const ext = name.split('.').pop().toLowerCase();
  return {mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm', mkv: 'video/x-matroska',
          jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif', webp: 'image/webp'}[ext] || '';
}

browser.browserAction.onClicked.addListener(() => browser.tabs.create({url: 'helper.html'}));
browser.runtime.onInstalled.addListener(() => browser.tabs.create({url: 'helper.html'}));
