const say = text => { document.getElementById('message').textContent = text; };

async function showStatus() {
  const {connected, waiting} = await browser.runtime.sendMessage({type: 'status'});
  const status = document.getElementById('status');
  status.classList.toggle('on', connected);
  status.textContent = connected ? 'Connected to Ducky’s Clip Organizer.' : 'Ducky’s Clip Organizer is not running. Open it to see your clips.';
  if (waiting) status.textContent += ` ${waiting} post${waiting === 1 ? '' : 's'} waiting to be logged.`;
}

document.getElementById('shapes').onclick = async () => {
  const {shapes: latest = {}} = await browser.storage.local.get('shapes');
  if (!Object.keys(latest).length) return say('Nothing seen yet. Open your Fansly profile and Statistics page first.');
  const url = URL.createObjectURL(new Blob([JSON.stringify(latest, null, 2)], {type: 'application/json'}));
  Object.assign(document.createElement('a'), {href: url, download: 'fansly data shapes.json'}).click();
  say('Saved "fansly data shapes.json" to Downloads: field names and types only. Send it back for the stats charts.');
};

showStatus();
setInterval(showStatus, 15000);
