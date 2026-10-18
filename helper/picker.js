const params = new URLSearchParams(location.search);
const platform = params.get('platform');
const multiple = params.get('multiple') === '1';
const $ = id => document.getElementById(id);
const selected = new Map();  // fp -> clip
let data = null;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.filter(c => c != null && c !== false));
  return node;
}

function tile(clip, planned) {
  const img = el('img', {src: `${APP}/thumb/${clip.fp}.jpg`, alt: '', loading: 'lazy'});
  img.onerror = () => img.remove();
  const posted = clip.posted ? `Posted here ${clip.posted}×` : 'Not posted here yet';
  const button = el('button', {class: 'tile', 'aria-pressed': !planned && selected.has(clip.fp) ? 'true' : 'false',
    title: clip.name, 'data-fp': planned ? null : clip.fp},
    el('span', {class: 'frame'}, img, planned ? el('span', {class: 'when'}, planned.at.slice(5)) : null),
    el('span', {class: 'name'}, clip.name),
    el('span', {class: 'meta'}, planned ? 'Planned · caption ready' : posted));
  if (planned) {
    button.onclick = () => attach([clip], planned);  // a planned post brings its caption: attach straight away
  } else {
    button.onclick = () => toggle(clip);
    button.ondblclick = () => attach([clip]);
  }
  return button;
}

function toggle(clip) {
  if (selected.has(clip.fp)) selected.delete(clip.fp);
  else {
    if (!multiple) selected.clear();
    selected.set(clip.fp, clip);
  }
  for (const t of document.querySelectorAll('.tile[data-fp]')) t.setAttribute('aria-pressed', String(selected.has(t.dataset.fp)));
  $('attach').disabled = !selected.size;
  $('attach').textContent = selected.size > 1 ? `Attach ${selected.size} clips` : 'Attach';
}

function section(title, note, tiles, all) {
  const bulk = all && all.length > 1 ? (multiple
    ? el('button', {class: 'ghost small', onclick: () => attach(all, null, true)}, `Attach all ${all.length}`)
    : el('span', {class: 'single'}, 'This upload box takes one clip at a time')) : null;
  return tiles.length ? el('section', {}, el('h2', {}, title, note ? el('span', {}, note) : null, bulk),
    el('div', {class: 'grid'}, ...tiles)) : null;
}

function render() {
  const term = $('search').value.trim().toLowerCase();
  const match = c => c.name.toLowerCase().includes(term);
  const planned = data.planned.filter(match);
  const inBatch = new Set(data.batches.flatMap(b => b.items.map(c => c.fp)));
  const library = data.library.filter(c => match(c) && !inBatch.has(c.fp));
  const sections = [
    section('Planned for ' + platform, 'Picks the clip and fills its caption', planned.map(p => tile(p, p))),
    ...data.batches.map((b, i) => section(`${i ? 'Batch' : 'Latest batch'} · ${b.name}`, `${b.items.length} clips`,
      b.items.filter(match).map(c => tile(c)), term ? null : b.items)),
    section('Library', 'Newest first', library.map(c => tile(c))),
  ].filter(Boolean);
  $('body').replaceChildren(...(sections.length ? sections : [el('p', {class: 'empty'},
    term ? 'No clips match that search.' : 'No clips yet. Add a folder in Ducky’s Clip Organizer, then try again.')]));
}

async function attach(clips, planned, bulk = false) {
  $('attach').disabled = true;
  $('attach').textContent = 'Attaching…';
  document.body.classList.add('busy');
  try {
    await browser.runtime.sendMessage({type: 'attach', planned, bulk, options: data.options || {},
      clips: clips.map(c => ({fp: c.fp, name: c.name, caption: c.caption || ''}))});
  } catch (e) {
    document.body.classList.remove('busy');
    $('attach').textContent = 'Attach';
    $('attach').disabled = !selected.size;
    $('sub').textContent = 'That clip could not be loaded. Is Ducky’s Clip Organizer still open?';
  }
}

$('close').onclick = () => browser.runtime.sendMessage({type: 'close-picker'});
$('browse').onclick = () => browser.runtime.sendMessage({type: 'browse'});
$('attach').onclick = () => attach([...selected.values()]);
$('search').oninput = () => data && render();
addEventListener('keydown', e => { if (e.key === 'Escape') browser.runtime.sendMessage({type: 'close-picker'}); });
$('heading').textContent = `Choose a clip for ${platform}`;

browser.runtime.sendMessage({type: 'picker-data', platform}).then(result => {
  data = result;
  render();
  $('search').focus();
}, () => {
  $('sub').textContent = 'Ducky’s Clip Organizer is not running on this computer.';
  $('body').replaceChildren(el('div', {class: 'offline'}, el('h2', {}, 'Open Ducky’s Clip Organizer'),
    el('p', {}, 'Start Ducky’s Clip Organizer from the Start menu or desktop, then click upload again. Or browse for a file instead.')));
});
