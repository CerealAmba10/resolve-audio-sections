// Post list in storage.local, clip files in IndexedDB. Both stay on this computer and survive restarts
// (for a permanently installed add-on; a temporary one is wiped when Firefox closes).
async function getPosts() {
  return (await browser.storage.local.get({posts: []})).posts;
}

async function setPosts(posts) {
  await browser.storage.local.set({posts});
}

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('post-helper', 2);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('files')) db.createObjectStore('files');
      // Stats snapshots: {path, time, data}, saved from responses Fansly's own pages loaded.
      if (!db.objectStoreNames.contains('stats')) db.createObjectStore('stats', {autoIncrement: true}).createIndex('path', 'path');
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function inStore(name, mode, action) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(name, mode);
    const request = action(tx.objectStore(name));
    tx.oncomplete = () => resolve(request && request.result);
    tx.onerror = () => reject(tx.error);
  });
}

const files = (mode, action) => inStore('files', mode, action);
const stats = (mode, action) => inStore('stats', mode, action);

// The next post to do for a site: earliest planned time that is not posted or skipped.
function nextPost(posts, platform) {
  const open = posts.filter(p => p.platform === platform && p.status === 'ready').sort((a, b) => a.at.localeCompare(b.at));
  const done = posts.filter(p => p.platform === platform && p.status !== 'ready').length;
  return open.length ? {...open[0], position: done + 1, total: done + open.length} : null;
}
