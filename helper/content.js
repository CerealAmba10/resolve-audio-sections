// Opera GX-style uploads: clicking the site's upload button opens a picker of Ducky’s Clip Organizer clips instead of the
// file dialog (hold Shift for the normal dialog). Picking fills the site's own form; the creator clicks Post.
(() => {
  const platform = platformFor(location.href);
  if (!platform) return;
  let target = null;      // the site's file input waiting for a clip
  let bypass = false;     // let the next click through to the normal file dialog
  let shift = false;
  let current = null;     // the clip just attached, until the creator says it was posted
  const overlay = document.createElement('div');
  const overlayRoot = overlay.attachShadow({mode: 'closed'});
  const card = document.createElement('div');
  card.id = 'post-helper-card';
  const cardRoot = card.attachShadow({mode: 'open'});

  addEventListener('keydown', e => { if (e.key === 'Shift') shift = true; }, true);
  addEventListener('keyup', e => { if (e.key === 'Shift') shift = false; }, true);
  addEventListener('blur', () => { shift = false; });

  // Sites open the dialog by clicking a (usually hidden) file input; catch that click before the dialog opens.
  document.addEventListener('click', event => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'file') return;
    if (input.accept && !/video|image|mp4|\*/i.test(input.accept)) return;
    if (bypass || shift) {
      bypass = false;
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    target = input;
    openPicker(input.multiple);
  }, true);

  function openPicker(multiple) {
    const src = browser.runtime.getURL(`picker.html?platform=${encodeURIComponent(platform)}&multiple=${multiple ? 1 : 0}`);
    overlayRoot.innerHTML = `<style>
      :host { all: initial; position: fixed; inset: 0; z-index: 2147483646; }
      .backdrop { position: absolute; inset: 0; background: rgba(12, 10, 8, 0.55); animation: fade 160ms ease-out; }
      iframe { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(980px, calc(100vw - 32px));
        height: min(680px, calc(100vh - 32px)); border: 0; border-radius: 14px; box-shadow: 0 30px 90px rgba(0, 0, 0, 0.55);
        background: #211e1b; animation: rise 200ms cubic-bezier(0.25, 1, 0.5, 1); }
      @keyframes fade { from { opacity: 0; } }
      @keyframes rise { from { opacity: 0; transform: translate(-50%, -48%); } }
      @media (prefers-reduced-motion: reduce) { .backdrop, iframe { animation: none; } }
    </style><div class="backdrop"></div><iframe title="Choose a clip" src="${src}"></iframe>`;
    overlayRoot.querySelector('.backdrop').onclick = closePicker;
    document.documentElement.appendChild(overlay);
    overlayRoot.querySelector('iframe').focus();
  }

  function closePicker() {
    overlay.remove();
  }

  addEventListener('keydown', e => { if (e.key === 'Escape' && overlay.isConnected) closePicker(); }, true);

  function visible(el) {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }

  // Biggest visible text box on the page is the caption box on these sites' post forms.
  function captionField() {
    return [...document.querySelectorAll('textarea, [contenteditable=""], [contenteditable="true"]')]
      .filter(el => !card.contains(el) && visible(el))
      .sort((a, b) => b.offsetWidth * b.offsetHeight - a.offsetWidth * a.offsetHeight)[0] || null;
  }

  function setText(el, text) {
    el.focus();
    if (el.isContentEditable) {
      document.execCommand('selectAll');
      document.execCommand('insertText', false, text);
      return;
    }
    // The native setter (not the page's) so frameworks such as React and Angular notice the change.
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
    el.dispatchEvent(new Event('input', {bubbles: true}));
  }

  const TYPES = {mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm', mkv: 'video/x-matroska',
                 jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', gif: 'image/gif', webp: 'image/webp'};

  async function attach(clips) {
    const transfer = new DataTransfer();
    for (const clip of clips) {
      const blob = await (await app('/file/' + clip.fp)).blob();
      transfer.items.add(new File([blob], clip.name, {type: TYPES[clip.name.split('.').pop().toLowerCase()] || ''}));
    }
    target.files = transfer.files;
    target.dispatchEvent(new Event('input', {bubbles: true}));
    target.dispatchEvent(new Event('change', {bubbles: true}));
  }

  browser.runtime.onMessage.addListener(async message => {
    if (message.type === 'auto-posted' && current) {
      // The site confirmed the upload: logged already, no button needed.
      const clip = current.clips.find(c => c.fp === message.fp && !c.posted);
      if (clip) Object.assign(clip, {posted: true, url: message.url});
      const done = current.clips.filter(c => c.posted).length;
      if (done === current.clips.length) stopWatching();
      renderCard(done === current.clips.length
        ? `Posted and logged${current.clips.length > 1 ? `: all ${done}` : ''}.`
        : `${done} of ${current.clips.length} posted and logged so far.`);
    }
    if (message.type === 'close-picker') closePicker();
    if (message.type === 'browse') {
      closePicker();
      bypass = true;
      target && target.click();
    }
    if (message.type === 'attach' && target) {
      try {
        await attach(message.clips);
      } catch (e) {
        closePicker();
        current = {clips: message.clips, planned: null, failed: true};
        renderCard('Those clips could not be loaded. Is Ducky’s Clip Organizer still open?');
        return;
      }
      closePicker();
      browser.runtime.sendMessage({type: 'expect-posts', clips: message.clips.map(c => ({fp: c.fp}))});
      const count = message.clips.length;
      let note = count === 1 ? 'Clip attached.' : `${count} clips attached.`;
      if (message.planned) {
        const field = captionField();
        if (field) {
          setText(field, message.planned.caption);
          note += ' Caption filled.';
        } else note += ' Use Copy for the caption once its box appears.';
      }
      current = {clips: message.clips, planned: message.planned, bulk: message.bulk, auto: true, step: null,
                 options: (message.planned && message.planned.options) || message.options || {}};
      if (message.planned) current.clips = current.clips.map(c => ({...c, caption: c.caption || message.planned.caption}));
      refill();
      renderCard(note + (message.bulk && platform === 'Fansly'
        ? ' Using FBuddy Bulk Posts? Paste the captions with "Paste texts" and keep Sequential order.'
        : " Check everything, then click the site's Post button."));
      watchForm();
    }
  });

  // Auto-fill: whenever a new step or video form appears, fill it straight away (never Next, Publish or Submit).
  let observer = null, filling = false, timer = null;
  async function fillNow(prefix) {
    if (!current || filling || !FILLERS[platform]) return;
    filling = true;
    let note;
    try {
      note = await FILLERS[platform](current.options, current.clips);
    } catch (e) {
      note = 'Something on this page could not be filled: ' + e.message;
    }
    filling = false;
    if (current) renderCard(prefix + note);
  }
  function check() {
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (!current || !current.auto || filling || !STEPS[platform]) return;
      const step = STEPS[platform]();
      if (!step || step === current.step) return;
      current.step = step;
      fillNow('Auto-filled. ');
    }, 600);
  }
  function watchForm() {
    if (!FILLERS[platform] || current.failed) return;
    if (!observer) observer = new MutationObserver(check);
    observer.observe(document.body, {childList: true, subtree: true});
    check();
  }
  function stopWatching() {
    if (observer) observer.disconnect();
    clearTimeout(timer);
  }

  const SHOWN = ['audience', 'price', 'tags', 'niches', 'categories', 'keep_audio', 'public', 'private'];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  function renderCard(note) {
    if (!current) {
      card.remove();
      return;
    }
    const planned = current.planned;
    const many = current.clips.length > 1;
    const posted = current.clips.filter(c => c.posted);
    const allPosted = posted.length === current.clips.length;
    // FBuddy "Paste texts" format: captions separated by a line containing only ---, in clip order.
    const captions = current.clips.map(c => c.caption || c.name.replace(/\.[^.]+$/, '')).join('\n---\n');
    const presetRow = current.options && current.options.preset && FILLERS[platform] ? [['preset', current.options.preset]] : [];
    const rows = allPosted ? posted.filter(c => c.url).map(c => ['posted', c.url]) : presetRow.concat(planned ? [['post at', planned.at], ['caption', planned.caption],
      ...SHOWN.filter(k => planned.options[k] !== undefined && planned.options[k] !== '').map(k => [k.replace('_', ' '), planned.options[k]])]
      : many ? [['captions', `${current.clips.length} captions, in clip order`, captions]] : []);
    cardRoot.innerHTML = `<style>
      :host { all: initial; position: fixed; right: 16px; bottom: 16px; z-index: 2147483645; }
      .card { font: 13px/1.45 "Segoe UI", system-ui, sans-serif; color: #f2eee8; background: #211e1b; border: 1px solid #3b3631;
        border-radius: 12px; box-shadow: 0 16px 48px rgba(0, 0, 0, 0.45); width: 340px; padding: 14px; }
      .title { font-weight: 600; margin-bottom: 8px; display: flex; justify-content: space-between; gap: 8px; }
      .row { display: grid; grid-template-columns: 72px 1fr auto; gap: 8px; align-items: start; margin: 5px 0; }
      .row b { color: #a69d91; font-weight: 500; }
      .row span { word-break: break-word; white-space: pre-wrap; }
      button { font: inherit; border-radius: 7px; padding: 5px 11px; cursor: pointer; border: 1px solid #4a443d; background: #2c2824; color: #e9e3da; }
      button:hover { background: #36312c; }
      .primary { background: #f0b440; border-color: #f0b440; color: #2a1f0a; font-weight: 600; }
      .primary:hover { background: #f5c25e; }
      .actions { display: flex; gap: 8px; margin-top: 10px; }
      .note { margin-top: 10px; color: #c9c1b6; }
      .auto { display: flex; align-items: center; gap: 6px; margin-top: 8px; color: #c9c1b6; cursor: pointer; }
      .auto input { accent-color: #f0b440; }
      .small { margin-top: 6px; color: #a69d91; font-size: 12px; }
    </style><div class="card"><div class="title"><span>${esc(many ? `${current.clips.length} clips` : current.clips[0].name)}</span>
      <span>${esc(platform)}</span></div>
      ${rows.map(([k, v, copy]) => `<div class="row"><b>${esc(k)}</b><span>${esc(v)}</span><button data-copy="${esc(copy ?? v)}">Copy</button></div>`).join('')}
      ${allPosted ? '' : !current.failed && FILLERS[platform] ? `<label class="auto"><input type="checkbox" data-act="auto" ${current.auto ? 'checked' : ''}>
        Auto-fill each step as it appears</label>` : ''}
      <div class="actions">${allPosted ? '<button class="primary" data-act="dismiss">Done</button>' : `
        ${!current.failed && FILLERS[platform] ? '<button data-act="fill">Fill again</button>' : ''}
        ${current.failed ? '' : `<button data-act="posted" title="Posts are logged by themselves once the site confirms them. Use this only if one wasn't caught.">Mark as posted</button>`}
        <button data-act="dismiss">Not posted</button>`}</div>
      ${allPosted || current.failed ? '' : '<div class="small">Logged automatically once the site confirms the post.</div>'}
      <div class="note">${esc(note)}</div></div>`;
    if (!card.isConnected) document.documentElement.appendChild(card);
  }

  cardRoot.addEventListener('change', event => {
    if (event.target.dataset.act !== 'auto' || !current) return;
    current.auto = event.target.checked;
    current.step = null;  // turning it back on fills what's on screen now
    if (current.auto) check();
  });

  cardRoot.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.copy !== undefined) {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = 'Copied';
      return;
    }
    if (button.dataset.act === 'posted') {
      // Backup for a post the site didn't confirm in a way Post Helper could see: log only the ones not logged yet.
      const result = await browser.runtime.sendMessage({type: 'posted', platform,
                                                        fps: current.clips.filter(c => !c.posted).map(c => c.fp)});
      current = null;
      stopWatching();
      renderCard();
      if (result && result.waiting) console.info('Post Helper: Ducky’s Clip Organizer is closed; the post is logged when it opens.');
    }
    if (button.dataset.act === 'fill') {
      button.disabled = true;
      button.textContent = 'Filling…';
      await fillNow('');
      return;
    }
    if (button.dataset.act === 'dismiss') {
      current = null;
      stopWatching();
      renderCard();
    }
  });
})();
