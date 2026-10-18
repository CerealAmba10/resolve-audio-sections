// Fansly stats snapshots in IndexedDB, kept on this computer only.
function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('post-helper', 2);
    request.onupgradeneeded = () => {
      const db = request.result;
      // Stats snapshots: {path, time, data}, saved from responses Fansly's own pages loaded.
      if (!db.objectStoreNames.contains('stats')) db.createObjectStore('stats', {autoIncrement: true}).createIndex('path', 'path');
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function stats(mode, action) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction('stats', mode);
    const request = action(tx.objectStore('stats'));
    tx.oncomplete = () => resolve(request && request.result);
    tx.onerror = () => reject(tx.error);
  });
}

// Clip Organizer, the desktop app on this computer that holds the clip library.
const APP = 'http://127.0.0.1:47833';

async function app(path, body) {
  const response = await fetch(APP + path, body === undefined ? {headers: {'X-Clip-Organizer': '1'}} : {
    method: 'POST', headers: {'X-Clip-Organizer': '1', 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!response.ok) throw new Error(`Clip Organizer answered ${response.status}`);
  return path.startsWith('/file/') ? response : response.json();
}
