const say = text => { document.getElementById('message').textContent = text; };

async function showStatus() {
  const {connected, waiting} = await browser.runtime.sendMessage({type: 'status'});
  const status = document.getElementById('status');
  status.classList.toggle('on', connected);
  status.textContent = connected ? 'Connected to Clip Organizer.' : 'Clip Organizer is not running. Open it to see your clips.';
  if (waiting) status.textContent += ` ${waiting} post${waiting === 1 ? '' : 's'} waiting to be logged.`;
}

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

showStatus();
setInterval(showStatus, 15000);
