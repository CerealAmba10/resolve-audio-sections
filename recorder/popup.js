const $ = id => document.getElementById(id);

async function render() {
  const [tab] = await browser.tabs.query({active: true, currentWindow: true});
  const {watching, steps} = await browser.runtime.sendMessage({type: 'state'});
  const url = tab && tab.url ? new URL(tab.url) : null;
  const supported = url && allowed(url.href);
  $('page').textContent = url ? `${url.hostname}${url.pathname}` : 'No page open';
  $('page').classList.toggle('off', !supported);
  $('count').textContent = steps ? `${steps} steps` : '';
  $('toggle').disabled = !watching && !supported;
  $('toggle').textContent = watching ? 'Stop watching' : 'Start watching';
  $('toggle').classList.toggle('stop', watching);
  $('hint').textContent = watching
    ? 'Watching. Click every button, menu and option on the upload page, then come back and stop.'
    : supported
      ? 'Start, then click through everything on the upload page. Typed text and passwords are never recorded.'
      : `This page isn't one of the sites it records (${SITES.map(s => s[0]).join(', ')}).`;
}

$('toggle').onclick = async () => {
  const {watching} = await browser.runtime.sendMessage({type: 'state'});
  await browser.runtime.sendMessage({type: 'watch', on: !watching});
  render();
};
$('review').onclick = () => { browser.tabs.create({url: 'review.html'}); window.close(); };
$('help').onclick = () => { browser.tabs.create({url: 'start.html'}); window.close(); };
render();
