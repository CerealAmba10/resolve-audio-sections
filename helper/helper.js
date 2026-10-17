const say = text => { document.getElementById('message').textContent = text; };
const SITE_URLS = {'Fansly': 'https://fansly.com/', 'RedGifs': 'https://www.redgifs.com/', 'Pornhub Shorts': 'https://www.pornhub.com/'};

async function render() {
  const body = document.getElementById('posts');
  body.textContent = '';
  for (const post of (await getPosts()).sort((a, b) => a.at.localeCompare(b.at))) {
    const row = body.insertRow();
    for (const text of [post.platform, post.at, post.file, post.caption, post.status]) row.insertCell().textContent = text;
    const cell = row.insertCell();
    const open = cell.appendChild(Object.assign(document.createElement('button'), {textContent: 'Open site'}));
    open.onclick = () => browser.tabs.create({url: post.options.url || SITE_URLS[post.platform]});
    if (post.status !== 'ready') {
      const again = cell.appendChild(Object.assign(document.createElement('button'), {textContent: 'Do again'}));
      again.onclick = () => setStatus(post.id, 'ready');
    }
  }
}

async function setStatus(id, status) {
  const posts = await getPosts();
  posts.find(p => p.id === id).status = status;
  await setPosts(posts);
}

document.getElementById('import').onchange = async event => {
  const chosen = [...event.target.files];
  const clips = new Map(chosen.filter(f => !f.name.endsWith('.json')).map(f => [f.name, f]));
  const batches = chosen.filter(f => f.name.endsWith('.json'));
  if (!batches.length) return say('Select batch.json together with the clips (Ctrl+A in the exported folder).');
  const posts = await getPosts();
  let added = 0;
  const problems = [];
  for (const batch of batches) {
    let items;
    try {
      items = JSON.parse(await batch.text());
    } catch (e) {
      problems.push(`${batch.name} is not a batch file.`);
      continue;
    }
    for (const item of items) {
      if (!(item.platform in HOSTS)) {
        problems.push(`${item.platform} posts are uploaded by hand under its terms: ${item.file}`);
        continue;
      }
      const clip = clips.get(item.file);
      if (!clip) {
        problems.push(`Missing clip: ${item.file}`);
        continue;
      }
      const id = item.platform + '|' + item.file;
      await files('readwrite', store => store.put(clip, id));
      const existing = posts.find(p => p.id === id);
      if (existing) Object.assign(existing, item);  // re-import updates details, keeps status
      else posts.push({...item, id, status: 'ready'});
      added += 1;
    }
  }
  await setPosts(posts);
  event.target.value = '';
  say(`Imported ${added} posts.` + (problems.length ? ' ' + problems.join(' ') : ''));
};

document.getElementById('clear').onclick = async () => {
  const posts = await getPosts();
  const done = posts.filter(p => p.status !== 'ready');
  for (const post of done) await files('readwrite', store => store.delete(post.id));
  await setPosts(posts.filter(p => p.status === 'ready'));
  say(`Removed ${done.length} posts and their stored clips.`);
};

// Keys and value types only: what Fansly's stats data looks like, with no values, names or IDs.
function shape(value) {
  if (Array.isArray(value)) return value.length ? [shape(value[0])] : [];
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[/^\d+$/.test(k) ? '{id}' : k] = shape(v);
    return out;
  }
  return value === null ? 'null' : typeof value;
}

document.getElementById('shapes').onclick = async () => {
  const latest = {};
  for (const snap of await stats('readonly', store => store.getAll())) latest[snap.path] = shape(snap.data);
  if (!Object.keys(latest).length) return say("No Fansly stats saved yet. Open Fansly's Statistics and Earnings pages first.");
  const url = URL.createObjectURL(new Blob([JSON.stringify(latest, null, 2)], {type: 'application/json'}));
  Object.assign(document.createElement('a'), {href: url, download: 'fansly data shapes.json'}).click();
  say('Saved "fansly data shapes.json" to Downloads: field names and types only. Send it back for the stats charts.');
};

browser.storage.onChanged.addListener(render);
render();
