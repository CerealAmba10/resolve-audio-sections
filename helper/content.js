// Opera GX-style uploads: clicking the site's upload button opens a picker of Clip Organizer clips instead of the
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

  function attach(files) {
    const transfer = new DataTransfer();
    for (const f of files) transfer.items.add(new File([f.bytes], f.name, {type: f.type}));
    target.files = transfer.files;
    target.dispatchEvent(new Event('input', {bubbles: true}));
    target.dispatchEvent(new Event('change', {bubbles: true}));
  }

  browser.runtime.onMessage.addListener(message => {
    if (message.type === 'close-picker') closePicker();
    if (message.type === 'browse') {
      closePicker();
      bypass = true;
      target && target.click();
    }
    if (message.type === 'attach' && target) {
      closePicker();
      attach(message.files);
      let note = message.files.length === 1 ? 'Clip attached.' : `${message.files.length} clips attached.`;
      if (message.planned) {
        const field = captionField();
        if (field) {
          setText(field, message.planned.caption);
          note += ' Caption filled.';
        } else note += ' Use Copy for the caption once its box appears.';
      }
      current = {...message.clip, planned: message.planned};
      renderCard(note + " Check everything, then click the site's Post button.");
    }
  });

  const SHOWN = ['audience', 'price', 'tags', 'niches', 'categories', 'keep_audio', 'public', 'private'];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  function renderCard(note) {
    if (!current) {
      card.remove();
      return;
    }
    const planned = current.planned;
    const rows = planned ? [['post at', planned.at], ['caption', planned.caption],
      ...SHOWN.filter(k => planned.options[k] !== undefined && planned.options[k] !== '').map(k => [k.replace('_', ' '), planned.options[k]])] : [];
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
    </style><div class="card"><div class="title"><span>${esc(current.name)}</span><span>${esc(platform)}</span></div>
      ${rows.map(([k, v]) => `<div class="row"><b>${esc(k)}</b><span>${esc(v)}</span><button data-copy="${esc(v)}">Copy</button></div>`).join('')}
      <div class="actions"><button class="primary" data-act="posted">I posted it</button><button data-act="dismiss">Not posted</button></div>
      <div class="note">${esc(note)}</div></div>`;
    if (!card.isConnected) document.documentElement.appendChild(card);
  }

  cardRoot.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.copy !== undefined) {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = 'Copied';
      return;
    }
    if (button.dataset.act === 'posted') {
      const result = await browser.runtime.sendMessage({type: 'posted', platform, fp: current.fp});
      current = null;
      renderCard();
      if (result && result.waiting) console.info('Post Helper: Clip Organizer is closed; the post is logged when it opens.');
    }
    if (button.dataset.act === 'dismiss') {
      current = null;
      renderCard();
    }
  });
})();
