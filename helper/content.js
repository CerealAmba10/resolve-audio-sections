// Shows the next planned post for this site and fills it into the site's own form on request.
// It never clicks the site's Post button: the creator checks everything and posts it themselves.
(() => {
  const host = document.createElement('div');
  host.id = 'post-helper-card';
  const root = host.attachShadow({mode: 'open'});
  let post = null;
  let hidden = false;

  const SHOWN = ['audience', 'price', 'tags', 'niches', 'categories', 'keep_audio', 'public', 'private', 'nsfw'];
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  function visible(el) {
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }

  // Biggest visible text box on the page is the caption box on these sites' post forms.
  function captionField() {
    return [...document.querySelectorAll('textarea, [contenteditable=""], [contenteditable="true"]')]
      .filter(el => !host.contains(el) && visible(el))
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

  async function fill() {
    const notes = [];
    const input = [...document.querySelectorAll('input[type=file]')].find(i => !i.accept || /video|mp4|\*/i.test(i.accept));
    if (!input) {
      notes.push('No upload button found on this page. Open the new post form, then click Fill again.');
    } else {
      const clip = await browser.runtime.sendMessage({type: 'clip', id: post.id, name: post.file});
      if (!clip) {
        notes.push('This clip is missing. Import the batch folder again in Post Helper.');
      } else {
        const transfer = new DataTransfer();
        transfer.items.add(new File([clip.bytes], clip.name, {type: clip.type}));
        input.files = transfer.files;
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        notes.push('Clip attached.');
      }
    }
    const field = captionField();
    if (field) {
      setText(field, post.caption);
      notes.push('Caption filled.');
    } else {
      notes.push('No caption box found yet. Use Copy once it appears.');
    }
    notes.push("Now set the schedule and options below, check everything, and click the site's Post button.");
    return notes.join(' ');
  }

  function render(message = '') {
    if (!post) {
      host.remove();
      return;
    }
    const options = SHOWN.filter(k => k in post.options && post.options[k] !== '')
      .map(k => `<div class="row"><b>${esc(k.replace('_', ' '))}</b><span>${esc(post.options[k])}</span>
                 <button data-copy="${esc(post.options[k])}">Copy</button></div>`).join('');
    root.innerHTML = `<style>
      :host { all: initial; position: fixed; right: 16px; bottom: 16px; z-index: 2147483647; }
      .card { font: 13px/1.4 "Segoe UI", sans-serif; color: #111; background: #fff; border: 1px solid #999;
              border-radius: 8px; box-shadow: 0 4px 16px #0004; width: 340px; padding: 12px; }
      .row { display: grid; grid-template-columns: 84px 1fr auto; gap: 6px; align-items: start; margin: 4px 0; }
      .row span { word-break: break-word; white-space: pre-wrap; }
      button { font: inherit; padding: 3px 8px; cursor: pointer; }
      .actions { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
      .note { margin-top: 8px; color: #333; }
      .mini { padding: 6px 10px; }
    </style>` + (hidden
      ? `<button class="mini" data-act="show">Post Helper: ${esc(post.platform)} ${post.position}/${post.total}</button>`
      : `<div class="card"><b>Post Helper · ${esc(post.platform)} ${post.position} of ${post.total}</b>
          <div class="row"><b>clip</b><span>${esc(post.file)}</span><span></span></div>
          <div class="row"><b>post at</b><span>${esc(post.at)}</span><button data-copy="${esc(post.at)}">Copy</button></div>
          <div class="row"><b>caption</b><span>${esc(post.caption)}</span><button data-copy="${esc(post.caption)}">Copy</button></div>
          ${options}
          <div class="actions"><button data-act="fill">Fill clip + caption</button>
            <button data-act="posted">I posted it</button><button data-act="skip">Skip</button>
            <button data-act="hide">Hide</button></div>
          <div class="note">${esc(message)}</div></div>`);
    if (!host.isConnected) document.documentElement.appendChild(host);
  }

  async function refresh(message) {
    post = await browser.runtime.sendMessage({type: 'next'});
    render(message);
  }

  root.addEventListener('click', async event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.copy !== undefined) {
      await navigator.clipboard.writeText(button.dataset.copy);
      button.textContent = 'Copied';
      return;
    }
    const act = button.dataset.act;
    if (act === 'fill') render(await fill());
    if (act === 'posted' || act === 'skip') {
      await browser.runtime.sendMessage({type: 'status', id: post.id, status: act === 'posted' ? 'posted' : 'skipped'});
      await refresh(act === 'posted' ? 'Marked posted. This is the next one.' : 'Skipped.');
    }
    if (act === 'hide' || act === 'show') {
      hidden = act === 'hide';
      render();
    }
  });

  browser.storage.onChanged.addListener(() => refresh());
  refresh();
})();
