const PLATFORMS = ['Fansly', 'RedGifs', 'Pornhub Shorts'];
const SWATCH = name => `p${PLATFORMS.indexOf(name) + 1 || 4}`;
const $ = id => document.getElementById(id);
const state = {folders: [], folder: localStorage.getItem('folder') || null, clips: [], status: '', selected: null,
               chosen: [], seed: 1, release: null};

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {headers: {'X-Clip-Organizer': '1'}} : {
    method: 'POST', headers: {'X-Clip-Organizer': '1', 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function toast(text, error = false) {
  const el = $('toast');
  el.textContent = text;
  el.classList.toggle('error', error);
  el.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.remove('show'), error ? 6000 : 3200);
}

const run = fn => async (...args) => { try { await fn(...args); } catch (e) { toast(e.message, true); } };

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.flat().filter(c => c != null && c !== false));
  return node;
}

function thumb(fp, cls = 'thumb') {
  const img = el('img', {class: cls, src: `/thumb/${fp}.jpg`, alt: '', loading: 'lazy'});
  img.onerror = () => img.removeAttribute('src');
  return img;
}

function ago(text) {
  if (!text) return '';
  const days = Math.floor((Date.now() - new Date(text.replace(' ', 'T'))) / 86400000);
  return days <= 0 ? 'today' : days === 1 ? 'yesterday' : days < 60 ? `${days} days ago` : `${Math.round(days / 30)} months ago`;
}

const STATUS = {fresh: 'Never posted', rested: 'Rested', recent: 'Recent'};
// "likeCount" -> "Likes", "tipAmount" -> "Tip amount": readable names for whatever numbers the site sends.
const label = key => {
  const words = key.replace(/yCount$/, 'ies').replace(/Count$/, 's').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/_/g, ' ').toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

/* Routing and shell */
function view() {
  const name = location.hash.slice(1) || 'library';
  for (const v of ['welcome', 'library', 'organize', 'batch', 'activity', 'sites']) $(`view-${v}`).hidden = true;
  for (const a of document.querySelectorAll('.nav a')) {
    if (a.dataset.view === name) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
  const needsFolder = !['activity', 'sites'].includes(name);
  if (needsFolder && !state.folder) {
    $('view-welcome').hidden = false;
    $('title').textContent = 'Welcome';
    $('folder-path').textContent = '';
    $('top-actions').textContent = '';
    return;
  }
  $(`view-${name}`).hidden = false;
  const folder = state.folders.find(f => f.path === state.folder);
  $('title').textContent = name === 'activity' ? 'Activity' : name === 'sites' ? 'Sites' : folder ? folder.name : '';
  $('folder-path').textContent = !needsFolder || !folder ? '' : folder.path;
  $('top-actions').replaceChildren(...(name === 'library' && folder ? [
    el('button', {class: 'ghost', onclick: run(rescan)}, 'Rescan'),
    el('button', {class: 'ghost', onclick: run(() => removeFolder(folder.path))}, 'Remove from library')] : []));
  run({library: loadLibrary, organize: loadOrganize, batch: loadBatch, activity: loadActivity, sites: loadSites}[name])();
}

async function refreshState() {
  const data = await api('/api/state');
  state.folders = data.folders;
  if (!state.folders.some(f => f.path === state.folder)) state.folder = state.folders[0]?.path || null;
  $('version').textContent = `v${data.version}`;
  $('helper-status').classList.toggle('on', data.helper);
  $('helper-status').title = data.helper ? 'Post Helper has been in touch recently' : 'Open Post Helper in Firefox to connect';
  if (data.release) showRelease(data.release);
  showFfmpeg(data.ffmpeg);
  $('folder-list').replaceChildren(...state.folders.map(f => el('li', {}, el('button', {
    'aria-current': f.path === state.folder ? 'true' : false, title: f.path,
    onclick: () => { state.folder = f.path; localStorage.setItem('folder', f.path); state.selected = null; refreshState().then(view); }},
    el('span', {class: 'name'}, f.name), el('span', {class: 'count'}, f.count),
    el('span', {class: 'fresh'}, f.fresh ? `${f.fresh} never posted` : 'all posted at least once')))));
}

async function addFolder() {
  const result = await api('/api/folders', {});
  if (result.cancelled) return;
  state.folder = result.path;
  localStorage.setItem('folder', result.path);
  toast(`Added ${result.count} clips.`);
  await refreshState();
  location.hash = '#library';
  view();
}

async function removeFolder(path) {
  if (!confirm('Remove this folder from the library? Files stay on disk, and posting history is kept.')) return;
  await api('/api/folders/remove', {path});
  state.folder = null;
  await refreshState();
  view();
}

async function rescan() {
  const result = await api('/api/scan', {folder: state.folder});
  toast(`${result.count} clips in this folder.`);
  await refreshState();
  view();
}

/* Library */
async function loadLibrary() {
  state.clips = await api(`/api/clips?folder=${encodeURIComponent(state.folder)}`);
  renderLibrary();
  if (state.selected) showDetail(state.selected);
}

function renderLibrary() {
  const term = $('search').value.trim().toLowerCase();
  const rows = state.clips.filter(c => (!state.status || c.status === state.status) && c.name.toLowerCase().includes(term));
  const counts = Object.fromEntries(Object.keys(STATUS).map(s => [s, state.clips.filter(c => c.status === s).length]));
  $('library-summary').textContent = `${state.clips.length} clips · ${counts.fresh} never posted · ${counts.recent} recent`;
  $('clip-rows').replaceChildren(...rows.map(c => el('tr', {
    'aria-selected': c.fp === state.selected ? 'true' : 'false', tabindex: 0,
    onclick: () => showDetail(c.fp), onkeydown: e => e.key === 'Enter' && showDetail(c.fp)},
    el('td', {}, thumb(c.fp)),
    el('td', {}, el('div', {class: 'clip-name'}, c.name.split('/').pop()),
      c.name.includes('/') ? el('div', {class: 'muted small'}, c.name.split('/').slice(0, -1).join(' / ')) : null),
    el('td', {}, el('span', {class: `status ${c.status}`}, STATUS[c.status])),
    el('td', {}, el('div', {class: 'posted'}, Object.keys(c.posts).length ? Object.entries(c.posts).map(([p, v]) =>
      el('span', {title: `${p}: last ${v.last}`}, el('i', {class: `swatch ${SWATCH(p)}`}), `${v.count}`)) : el('span', {class: 'muted'}, '–'))),
    el('td', {class: 'num muted'}, ago(c.last) || '–'))));
  $('library-empty').hidden = rows.length > 0;
  $('library-empty').textContent = state.clips.length
    ? 'No clips match this filter.'
    : 'No clips in this folder yet. Add videos or images, then click Rescan.';
}

async function showDetail(fp) {
  state.selected = fp;
  renderLibrary();
  const d = await api(`/api/clip/${fp}`);
  const panel = $('detail');
  panel.hidden = false;
  panel.parentElement.classList.add('has-detail');
  const select = el('select', {'aria-label': 'Site'}, ...[...PLATFORMS, 'Instagram', 'TikTok', 'Reddit'].map(p => el('option', {}, p)));
  const close = () => {
    state.selected = null;
    panel.hidden = true;
    panel.parentElement.classList.remove('has-detail');
    renderLibrary();
  };
  panel.replaceChildren(...[
    el('button', {class: 'close', 'aria-label': 'Close details', onclick: close}, '✕'),
    thumb(fp, ''),
    el('div', {}, el('h2', {}, d.name), el('div', {class: 'muted'}, `${(d.size / 1048576).toFixed(1)} MB`)),
    el('div', {}, el('h3', {}, `Posted ${d.posts.length} time${d.posts.length === 1 ? '' : 's'}`),
      d.posts.length ? el('ul', {}, d.posts.map(p => el('li', {}, el('span', {}, el('i', {class: `swatch ${SWATCH(p.platform)}`}), ' ', p.platform),
        el('span', {class: 'muted'}, p.at)))) : el('p', {class: 'muted'}, 'Not posted yet.')),
    Object.keys(d.totals).length ? el('div', {}, el('h3', {}, `How it did · ${d.site_posts.length} post${d.site_posts.length === 1 ? '' : 's'}`),
      el('ul', {}, Object.entries(d.totals).map(([k, v]) => el('li', {}, el('span', {}, label(k)),
        el('span', {class: 'num'}, v.toLocaleString())))),
      el('p', {class: 'muted small'}, 'Added up across every post of this clip that Post Helper has seen.')) : null,
    d.site_posts.length ? el('div', {}, el('h3', {}, 'Its posts'), el('ul', {}, d.site_posts.map(p => el('li', {},
      p.url ? el('a', {href: p.url, target: '_blank', rel: 'noreferrer'}, `${p.platform} · ${p.created || 'post ' + p.post_id}`)
            : el('span', {}, `${p.platform} · ${p.created || p.post_id}`),
      el('span', {class: 'muted'}, Object.entries(p.counters).slice(0, 2).map(([k, v]) => `${v.toLocaleString()} ${label(k).toLowerCase()}`).join(' · ')))))) : null,
    d.planned.filter(p => !p.done).length ? el('div', {}, el('h3', {}, 'Planned'),
      el('ul', {}, d.planned.filter(p => !p.done).map(p => el('li', {}, el('span', {}, p.platform), el('span', {class: 'muted'}, p.at))))) : null,
    d.batches.length ? el('div', {}, el('h3', {}, 'In batches'), el('ul', {}, d.batches.map(b => el('li', {},
      el('button', {class: 'link', onclick: run(() => api('/api/open', {path: b.path}))}, b.path.split(/[\\/]/).pop()),
      el('span', {class: 'muted'}, ago(b.created)))))) : null,
    el('div', {}, el('h3', {}, 'Log a post'), el('div', {class: 'log-post'}, select,
      el('button', {class: 'ghost', onclick: run(async () => {
        await api('/api/posted', {fp, platform: select.value, source: 'manual'});
        toast(`Logged a ${select.value} post.`);
        await refreshState();
        await loadLibrary();
      })}, 'Log')))].filter(Boolean));
}

/* Organize */
let renameTimer;
function loadOrganize() {
  const folder = state.folders.find(f => f.path === state.folder);
  if (!$('prefix').value && folder?.prefix) $('prefix').value = folder.prefix;
  $('autosort').checked = !!folder?.autosort;
  previewRename();
  run(previewSort)();
}

function movesList(box, plan, empty) {
  box.replaceChildren(...(plan.length ? plan.map(([a, b]) => el('div', {}, el('span', {}, a), el('span', {class: 'arrow'}, '→'),
    el('span', {class: 'new'}, b))) : [el('p', {}, empty)]));
}

async function previewSort() {
  let plan;
  try {
    ({plan} = await api('/api/sort', {folder: state.folder}));
  } catch (e) {
    $('sort-preview').replaceChildren(el('p', {class: 'note'}, e.message));
    $('apply-sort').disabled = true;
    return;
  }
  movesList($('sort-preview'), plan, 'Every clip is already in its year and month folder.');
  $('apply-sort').disabled = !plan.length;
  $('apply-sort').textContent = plan.length ? `Sort ${plan.length} file${plan.length === 1 ? '' : 's'}` : 'Sort files';
}

async function applySort() {
  const button = $('apply-sort');
  button.classList.add('busy');
  try {
    const {plan} = await api('/api/sort', {folder: state.folder, apply: true});
    toast(`Sorted ${plan.length} files into year and month folders.`);
    await refreshState();
    await previewSort();
    previewRename();
  } finally {
    button.classList.remove('busy');
  }
}

async function previewRename() {
  const prefix = $('prefix').value.trim();
  $('prefix-example').textContent = /\{(N|DATE|NAME)/.test(prefix)
    ? '{N} keeps the number already in each name (free numbers for files without one). {DATE} keeps the date in the name, ' +
      'or uses the date the file was saved.'
    : `Just a name numbers files oldest first: "${prefix || 'SFW'} 1", "${prefix || 'SFW'} 2"… Files already named that way keep their number.`;
  const box = $('rename-preview');
  if (!prefix) {
    box.replaceChildren(el('p', {}, 'Type a name to see what each file will be called.'));
    $('apply-rename').disabled = true;
    return;
  }
  try {
    const {plan, notes} = await api('/api/rename', {folder: state.folder, prefix});
    movesList(box, plan, 'Every file already follows this name.');
    box.prepend(...notes.map(n => el('p', {class: 'note'}, n)));
    $('apply-rename').disabled = !plan.length;
    $('apply-rename').textContent = plan.length ? `Rename ${plan.length} file${plan.length === 1 ? '' : 's'}` : 'Rename files';
  } catch (e) {
    box.replaceChildren(el('p', {}, e.message));
    $('apply-rename').disabled = true;
  }
}

async function applyRename() {
  const button = $('apply-rename');
  button.classList.add('busy');
  try {
    const {plan} = await api('/api/rename', {folder: state.folder, prefix: $('prefix').value.trim(), apply: true});
    toast(`Renamed ${plan.length} files. Their posting history came with them.`);
    await refreshState();
    previewRename();
  } finally {
    button.classList.remove('busy');
  }
}

/* Batch */
let batchTimer;
function loadBatch() {
  $('batch-name').placeholder = new Date().toLocaleDateString('en-US', {month: 'long', year: 'numeric'});
  run(renderPlatforms)();
  previewBatch();
}

async function previewBatch() {
  const result = await api('/api/batch/preview', {folder: state.folder, count: +$('count').value || 1,
    recent_days: +$('recent').value || 0, window: +$('window').value || 1, seed: state.seed});
  state.chosen = result.chosen;
  renderBatch(result.total);
}

function renderBatch(total) {
  $('batch-list').replaceChildren(...state.chosen.map((c, i) => el('li', {},
    thumb(c.fp), el('span', {class: 'clip-name'}, c.name),
    el('span', {class: `reason ${c.reason === 'never posted' ? 'fresh' : ''}`}, c.reason),
    el('button', {class: 'remove', 'aria-label': `Leave out ${c.name}`, title: 'Leave out', onclick: () => {
      state.chosen.splice(i, 1);
      renderBatch(total);
    }}, '✕'))));
  const want = +$('count').value || 0;
  $('batch-summary').textContent = state.chosen.length < want && total !== undefined
    ? `Only ${state.chosen.length} of ${want} available: the rest were used too recently.`
    : `${state.chosen.length} clips will be copied into a new folder.`;
  $('create-batch').disabled = !state.chosen.length;
  $('create-batch').textContent = `Create batch of ${state.chosen.length}`;
}

async function createBatch() {
  const platforms = [...document.querySelectorAll('.platforms input:checked')].map(i => i.value);
  const presets = Object.fromEntries([...document.querySelectorAll('.platforms select')].map(s => [s.name, Number(s.value)]));
  const button = $('create-batch');
  button.classList.add('busy');
  try {
    const result = await api('/api/batch', {folder: state.folder, names: state.chosen.map(c => c.name), presets,
      platforms, name: $('batch-name').value.trim()});
    toast(`Copied ${result.copied} clips into "${result.name}"` + (platforms.length ? `, ${result.planned} posts planned for Post Helper.` : '.') +
      (result.skipped.length ? ` ${result.skipped.length} skipped by site rules.` : ''));
    await api('/api/open', {path: result.path});
    $('batch-name').value = '';
    state.seed += 1;
    await refreshState();
    previewBatch();
  } finally {
    button.classList.remove('busy');
  }
}

/* Activity */
async function loadActivity() {
  const data = await api('/api/activity');
  const used = PLATFORMS.concat(Object.keys(Object.assign({}, ...data.weeks.map(w => w.platforms))).filter(p => !PLATFORMS.includes(p)));
  const series = used.filter(p => data.weeks.some(w => w.platforms[p]));
  $('legend').replaceChildren(...series.map(p => el('span', {}, el('i', {class: `swatch ${SWATCH(p)}`}), p)));
  drawChart(data.weeks, series);
  $('chart-table').replaceChildren(el('tr', {}, el('th', {}, 'Week of'), ...series.map(p => el('th', {class: 'num'}, p))),
    ...data.weeks.map(w => el('tr', {}, el('td', {}, w.week), ...series.map(p => el('td', {class: 'num'}, w.platforms[p] || 0)))));
  $('recent-rows').replaceChildren(...data.recent.map(r => el('tr', {}, el('td', {}, r.at),
    el('td', {}, el('span', {class: 'posted'}, el('span', {}, el('i', {class: `swatch ${SWATCH(r.platform)}`}), r.platform))),
    el('td', {}, r.name || 'Unknown clip'), el('td', {class: 'muted'}, r.source === 'post-helper' ? 'Post Helper' : 'You'))));
  $('activity-empty').hidden = data.recent.length > 0;
  const keys = [...new Set(data.watched.flatMap(p => Object.keys(p.counters)))].slice(0, 6);
  $('watched').hidden = !data.watched.length;
  $('watched-table').replaceChildren(
    el('tr', {}, el('th', {}, 'Posted'), el('th', {}, 'Clip'), ...keys.map(k => el('th', {class: 'num'}, label(k))),
      el('th', {class: 'num'}, 'Checked')),
    ...data.watched.map(p => el('tr', {}, el('td', {}, p.url
        ? el('a', {href: p.url, target: '_blank', rel: 'noreferrer'}, p.created || 'Open post') : p.created || '–'),
      el('td', {}, p.name ? p.name.split('/').pop() : el('span', {class: 'muted'}, 'Not from Post Helper')),
      ...keys.map(k => el('td', {class: 'num'}, k in p.counters ? p.counters[k].toLocaleString() : '–')),
      el('td', {class: 'num muted'}, ago(p.updated)))));
}

function drawChart(weeks, series) {
  const W = 760, H = 220, left = 28, bottom = 22, top = 8;
  const totals = weeks.map(w => series.reduce((n, p) => n + (w.platforms[p] || 0), 0));
  const max = Math.max(4, ...totals);
  const step = Math.ceil(max / 4);
  const y = v => H - bottom - (H - bottom - top) * v / (step * 4);
  const slot = (W - left) / weeks.length, bar = Math.min(34, slot * 0.6);
  let svg = '';
  for (let i = 0; i <= 4; i++) svg += `<line x1="${left}" x2="${W}" y1="${y(i * step)}" y2="${y(i * step)}" stroke="var(--line-soft)"/>
    <text x="${left - 8}" y="${y(i * step) + 4}" text-anchor="end">${i * step}</text>`;
  weeks.forEach((w, i) => {
    const x = left + slot * i + (slot - bar) / 2;
    let base = 0;
    series.forEach(p => {
      const v = w.platforms[p] || 0;
      if (!v) return;
      const y0 = y(base), y1 = y(base + v);
      // 2px surface gap between stacked segments; rounded top on the last one.
      svg += `<rect class="bar" data-tip="${p}: ${v} · week of ${w.week}" x="${x}" y="${y1}" width="${bar}" height="${Math.max(0, y0 - y1 - 2)}"
        rx="${base + v === totals[i] ? 4 : 0}" fill="var(--${SWATCH(p)})"/>`;
      base += v;
    });
    if (i % 2 === 0) svg += `<text x="${x + bar / 2}" y="${H - 6}" text-anchor="middle">${new Date(w.week).toLocaleDateString(undefined, {month: 'short', day: 'numeric'})}</text>`;
  });
  const chart = $('chart');
  chart.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${svg}</svg>`;
  chart.setAttribute('aria-label', `Posts per week for the last 12 weeks: ${totals.join(', ')}`);
}

function showFfmpeg(status) {
  $('ffmpeg-banner').hidden = status === 'ready';
  $('get-ffmpeg').hidden = status === 'downloading';
  $('ffmpeg-text').textContent = {
    downloading: 'Downloading FFmpeg… thumbnails appear when it finishes.',
    error: 'FFmpeg could not be downloaded. Check the internet connection and try again.',
  }[status] || 'Thumbnails and site length checks need FFmpeg, a free video tool (about 90 MB, downloaded once and checksum-verified).';
  if (status === 'downloading') setTimeout(run(refreshState), 3000);
}

/* Sites: named presets per site (tags, categories, captions, times...), chosen per batch and in Post Helper. */
const FIELD_ORDER = ['tags', 'categories', 'niches', 'audience', 'who', 'shorties', 'private', 'caption', 'times', 'clip',
                     'max_seconds', 'max_mb', 'vertical', 'nsfw', 'keep_audio', 'price', 'min_tags', 'min_categories',
                     'subreddit', 'flair', 'url'];
const LISTS = ['tags', 'categories', 'niches'];  // edited as chips
const HIDDEN = ['enabled', 'preset'];
const FIELD_HELP = {
  tags: 'Type a tag and press Enter. Suggestions are tags this site has shown on its own upload page.',
  categories: "Pornhub needs 2 to 8, from its own category list.",
  niches: 'As named on RedGifs.', audience: 'Straight, Gay, Lesbian, Trans, Bisexual or Animated.',
  who: '"Performer(s)" or "Solo/Animation" (Pornhub), or leave empty.',
  shorties: 'Turns on Pornhub\'s "Publish to Shorties Only" switch for each video.',
  caption: 'Uses {name} (the clip name) and {tags}.', times: 'Daily posting times, e.g. 12:00, 20:00.',
  clip: 'teaser, full or any.', max_seconds: '0 means no limit.', max_mb: '0 means no limit.', price: '0 means free.',
  url: 'The page "Open site" goes to.',
};
const FIELD_NAME = {who: 'Who is in it', max_seconds: 'Longest clip (seconds)', max_mb: 'Largest file (MB)', nsfw: 'NSFW',
                    keep_audio: 'Keep audio', shorties: 'Publish to Shorties only', min_tags: 'Fewest tags',
                    min_categories: 'Fewest categories', url: 'Upload page'};
let presetData = null, presetId = null;
const splitList = text => String(text || '').split(',').map(t => t.trim().replace(/^#/, '')).filter(Boolean);

async function loadPresets() {
  presetData = await api('/api/presets');
  return presetData;
}

async function loadSites() {
  await loadPresets();
  if (!presetData.presets.some(p => p.id === presetId)) presetId = presetData.presets[0].id;
  renderSites();
}

function renderSites() {
  const current = presetData.presets.find(p => p.id === presetId);
  $('preset-list').replaceChildren(...presetData.sites.map(site => el('div', {class: 'preset-group'},
    el('div', {class: 'preset-site'}, el('i', {class: `swatch ${SWATCH(site)}`}), site,
      presetData.helper.includes(site) ? null : el('span', {class: 'muted small'}, ' manual')),
    ...presetData.presets.filter(p => p.platform === site).map(p => el('button', {
      class: 'preset-item', 'aria-current': p.id === presetId ? 'true' : false,
      onclick: () => { presetId = p.id; renderSites(); }}, p.name)),
    el('button', {class: 'link small add-preset', onclick: run(() => newPreset(site))}, '+ New preset'))));
  $('preset-site').textContent = `${current.platform} preset name`;
  $('preset-name').value = current.name;
  $('site-note').textContent = presetData.helper.includes(current.platform)
    ? 'Post Helper fills these into the site\'s form, and batches use the times and limits.'
    : "This site's terms don't allow automation: presets here only shape planning and exported captions.";
  const known = presetData.tags[current.platform] || {};
  const values = current.settings;
  const keys = FIELD_ORDER.filter(k => k in values)
    .concat(Object.keys(values).filter(k => !FIELD_ORDER.includes(k) && !HIDDEN.includes(k)));
  $('site-form').replaceChildren(...keys.map(key => field(key, values[key], known[key] || [], current.platform)));
}

function field(key, value, known, site) {
  const title = FIELD_NAME[key] || label(key);
  const help = FIELD_HELP[key] ? el('small', {class: 'muted'}, FIELD_HELP[key]) : null;
  if (typeof value === 'boolean') {
    return el('label', {class: 'field check'}, el('input', {type: 'checkbox', name: key, checked: value}), el('span', {}, title), help);
  }
  if (LISTS.includes(key)) return chips(key, title, value, known, site, help);
  return el('label', {class: 'field'}, el('span', {}, title),
    el('input', {name: key, type: typeof value === 'number' ? 'number' : 'text', step: 'any', value: String(value)}), help);
}

// Tag-style list: each entry a chip; entries the site has never suggested are flagged before you post.
function chips(key, title, value, known, site, help) {
  const items = splitList(value);
  const lower = new Set(known.map(k => k.toLowerCase()));
  const listId = `known-${key}`;
  const box = el('div', {class: 'chips', 'data-name': key});
  const input = el('input', {list: listId, placeholder: items.length ? '' : `Add ${key}`, 'aria-label': `Add ${key}`});
  const draw = () => {
    box.replaceChildren(...items.map((item, i) => el('span', {
      class: `chip${known.length && !lower.has(item.toLowerCase()) ? ' unknown' : ''}`,
      title: known.length && !lower.has(item.toLowerCase()) ? `${site} hasn't suggested this yet. Check the spelling.` : ''},
      item, el('button', {type: 'button', 'aria-label': `Remove ${item}`, onclick: () => { items.splice(i, 1); draw(); }}, '✕'))), input);
    box.dataset.value = items.join(', ');
  };
  const add = () => {
    for (const item of splitList(input.value)) if (!items.some(x => x.toLowerCase() === item.toLowerCase())) items.push(item);
    input.value = '';
    draw();
    input.focus();
  };
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); add(); }
    if (e.key === 'Backspace' && !input.value && items.length) { items.pop(); draw(); input.focus(); }
  });
  input.addEventListener('change', () => { if (known.includes(input.value)) add(); });  // picked from suggestions
  input.addEventListener('blur', () => { if (input.value.trim()) add(); });
  draw();
  const count = known.length ? el('small', {class: 'muted'}, `${known.length} names learned from ${site}.`) : null;
  return el('div', {class: 'field wide'}, el('span', {}, title), box,
    el('datalist', {id: listId}, ...known.map(k => el('option', {value: k}))), help, count);
}

function formValues() {
  const values = {};
  for (const input of $('site-form').querySelectorAll('input[name]')) {
    values[input.name] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
  }
  for (const box of $('site-form').querySelectorAll('.chips')) values[box.dataset.name] = box.dataset.value;
  return values;
}

async function saveSite() {
  const current = presetData.presets.find(p => p.id === presetId);
  const saved = await api('/api/presets', {id: presetId, platform: current.platform, name: $('preset-name').value, values: formValues()});
  toast(`Saved "${saved.name}" for ${saved.platform}.`);
  await loadSites();
}

async function newPreset(site, from) {
  const base = from || presetData.presets.find(p => p.platform === site);
  let name = from ? `${from.name} copy` : 'New preset', n = 1;
  while (presetData.presets.some(p => p.platform === site && p.name === name)) name = `${from ? from.name + ' copy' : 'New preset'} ${++n}`;
  const saved = await api('/api/presets', {platform: site, name, values: from ? formValues() : base.settings});
  presetId = saved.id;
  await loadSites();
  $('preset-name').select();
}

async function deletePreset() {
  const current = presetData.presets.find(p => p.id === presetId);
  if (!confirm(`Delete the "${current.name}" preset for ${current.platform}?`)) return;
  await api('/api/presets/delete', {id: presetId});
  presetId = null;
  await loadSites();
}

// Batch view: tick sites to plan, each with its preset.
async function renderPlatforms() {
  const data = await loadPresets();
  const fieldset = $('platforms');
  const ticked = new Set([...fieldset.querySelectorAll('input:checked')].map(i => i.value));
  const chosen = Object.fromEntries([...fieldset.querySelectorAll('select')].map(s => [s.name, s.value]));
  fieldset.replaceChildren(fieldset.querySelector('legend') || el('legend', {}, 'Also plan posts for Post Helper'),
    ...data.helper.map(site => el('div', {class: 'platform-choice'},
      el('label', {}, el('input', {type: 'checkbox', value: site, checked: ticked.has(site)}),
        el('i', {class: `swatch ${SWATCH(site)}`}), site),
      el('select', {name: site, 'aria-label': `${site} preset`},
        ...data.presets.filter(p => p.platform === site).map(p => el('option', {value: p.id, selected: String(p.id) === chosen[site]}, p.name))))));
}

/* Updates */
function showRelease(release) {
  const box = $('update');
  if (release.available) {
    box.replaceChildren(el('span', {}, `v${release.version} available`), el('button', {class: 'link', onclick: run(installUpdate)}, 'Install'));
  }
}

async function checkUpdate() {
  $('check-update').textContent = 'Checking…';
  try {
    const release = await api('/api/update', {});
    if (!release.available) $('check-update').textContent = 'Up to date';
    showRelease(release);
  } catch (e) {
    $('check-update').textContent = 'Check for updates';
    throw e;
  }
}

async function installUpdate() {
  if (!confirm('Install the update now? Ducky’s Clip Organizer restarts and this page reloads.')) return;
  await api('/api/update', {install: true});
  toast('Installing the update…');
  setTimeout(() => location.reload(), 4000);
}

/* Wiring */
$('add-folder').onclick = run(addFolder);
$('welcome-add').onclick = run(addFolder);
$('folder-path').onclick = run(() => api('/api/open', {path: state.folder}));
$('check-update').onclick = run(checkUpdate);
$('get-ffmpeg').onclick = run(async () => showFfmpeg((await api('/api/ffmpeg', {})).ffmpeg));
$('search').oninput = renderLibrary;
for (const b of document.querySelectorAll('#status-filter button')) b.onclick = () => {
  state.status = b.dataset.status;
  for (const o of document.querySelectorAll('#status-filter button')) o.setAttribute('aria-checked', String(o === b));
  renderLibrary();
};
$('prefix').oninput = () => { clearTimeout(renameTimer); renameTimer = setTimeout(previewRename, 200); };
for (const b of document.querySelectorAll('.token')) b.onclick = () => {
  const input = $('prefix');
  const at = input.selectionStart ?? input.value.length;
  const before = input.value.slice(0, at), after = input.value.slice(input.selectionEnd ?? at);
  const pad = before && !before.endsWith(' ') ? ' ' : '';
  input.value = before + pad + b.dataset.token + after;
  input.focus();
  input.setSelectionRange(before.length + pad.length + b.dataset.token.length, before.length + pad.length + b.dataset.token.length);
  previewRename();
};
$('apply-rename').onclick = run(applyRename);
$('apply-sort').onclick = run(applySort);
$('save-site').onclick = run(saveSite);
$('site-form').onsubmit = e => { e.preventDefault(); run(saveSite)(); };
$('duplicate-preset').onclick = run(() => newPreset(presetData.presets.find(p => p.id === presetId).platform,
                                                     presetData.presets.find(p => p.id === presetId)));
$('delete-preset').onclick = run(deletePreset);
$('autosort').onchange = run(async () => {
  const on = $('autosort').checked;
  await api('/api/sort', {folder: state.folder, autosort: on});
  toast(on ? 'New clips in this folder will be sorted automatically.' : 'Automatic sorting is off for this folder.');
  await refreshState();
});
for (const id of ['count', 'recent', 'window']) $(id).oninput = () => { clearTimeout(batchTimer); batchTimer = setTimeout(run(previewBatch), 250); };
$('reshuffle').onclick = run(() => { state.seed += 1; return previewBatch(); });
$('create-batch').onclick = run(createBatch);
$('chart').addEventListener('mousemove', e => {
  const tip = $('tooltip');
  const text = e.target.dataset?.tip;
  tip.hidden = !text;
  if (text) Object.assign(tip.style, {left: `${e.clientX + 12}px`, top: `${e.clientY - 30}px`}), tip.textContent = text;
});
$('chart').addEventListener('mouseleave', () => { $('tooltip').hidden = true; });
window.addEventListener('hashchange', run(view));
setInterval(run(refreshState), 30000);
run(async () => { await refreshState(); view(); })();
