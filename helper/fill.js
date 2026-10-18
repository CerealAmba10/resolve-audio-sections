// Per-site "Fill this page", mapped from recordings of each site's real upload page. Fills fields only: it never
// clicks Next, Publish or Submit, and never ticks Pornhub's legal certification boxes. The creator does those.
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const list = text => String(text || '').split(',').map(t => t.trim().replace(/^#/, '')).filter(Boolean);
const same = (a, b) => a.trim().toLowerCase() === b.trim().toLowerCase();
const shown = el => { const b = el.getBoundingClientRect(); return b.width > 0 && b.height > 0; };

function type(el, text) {
  el.focus();
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);  // native setter, so React/Vue notice
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: text.slice(-1) || 'a'}));
}

function enter(el) {
  for (const kind of ['keydown', 'keypress', 'keyup']) {
    el.dispatchEvent(new KeyboardEvent(kind, {bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', keyCode: 13, which: 13}));
  }
}

// A suggestion list near an input (sites render it just below the box).
function nearby(input, selector) {
  for (let node = input.parentElement, i = 0; node && i < 6; node = node.parentElement, i++) {
    const found = [...node.querySelectorAll(selector)].filter(shown);
    if (found.length) return found;
  }
  return [];
}

// Type a value and pick the matching suggestion; returns false if the site offered no exact match.
async function pick(input, value, selector, freeText) {
  type(input, value);
  await pause(700);
  const match = nearby(input, selector).find(el => same(el.textContent, value));
  if (match) {
    match.click();
    await pause(200);
    return true;
  }
  if (freeText) {
    enter(input);
    await pause(200);
    return true;
  }
  return false;
}

const FILLERS = {
  // RedGifs is a wizard: fill whichever step is on screen, then the creator clicks Next.
  async 'RedGifs'(options, clips) {
    const notes = [];
    const audience = document.querySelector(`input[type=radio][name="${String(options.audience || 'Straight').toLowerCase()}"]`);
    if (audience && shown(audience.closest('label, li') || audience)) {
      if (!audience.checked) audience.click();
      notes.push(`Audience: ${options.audience || 'Straight'}.`);
    }
    const tagBox = [...document.querySelectorAll('input[placeholder^="Type to search for tags"]')].find(shown);
    if (tagBox) {
      const missing = [];
      for (const tag of list(options.tags)) {
        type(tagBox, tag);
        await pause(700);
        const button = [...document.querySelectorAll('button')].find(b =>
          shown(b) && same(((b.getAttribute('aria-label') || b.textContent).match(/^Select (.+) tag$/i) || [])[1] || '', tag));
        if (button) button.click(); else missing.push(tag);
        await pause(200);
      }
      type(tagBox, '');
      notes.push(list(options.tags).length ? 'Tags added.' : 'No tags set for RedGifs in Settings.');
      if (missing.length) notes.push(`RedGifs has no tag called: ${missing.join(', ')}.`);
    }
    const nicheBox = document.querySelector('input[type=checkbox][name]') &&
      [...document.querySelectorAll('input[placeholder="Search..."]')].find(shown);
    if (nicheBox) {
      const missing = [];
      for (const niche of list(options.niches)) {
        const slug = niche.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
        let box = document.querySelector(`input[type=checkbox][name="${slug}"]`);
        if (!box) {
          type(nicheBox, niche);
          await pause(700);
          box = document.querySelector(`input[type=checkbox][name="${slug}"]`);
        }
        if (box) { if (!box.checked) box.click(); } else missing.push(niche);
      }
      type(nicheBox, '');
      notes.push(list(options.niches).length ? 'Niches ticked.' : 'No niches set for RedGifs in Settings.');
      if (missing.length) notes.push(`Couldn't find these niches: ${missing.join(', ')}.`);
    }
    const description = [...document.querySelectorAll('textarea[placeholder^="Give your content"]')].find(shown);
    if (description) {
      if (!description.disabled && clips[0] && clips[0].caption) {
        type(description, clips[0].caption);
        notes.push('Description filled.');
      }
      const privateLabel = [...document.querySelectorAll('*')].find(el => el.children.length === 0 && same(el.textContent, 'Make Private'));
      const toggle = privateLabel && nearby(privateLabel, 'input[type=checkbox]')[0];
      if (toggle && toggle.checked !== !!options.private) toggle.click();
      if (toggle) notes.push(options.private ? 'Set to private.' : 'Left public.');
    }
    if (!notes.length) notes.push('Nothing to fill on this step.');
    return notes.join(' ') + ' Check it, then click Next (or Publish on the last step).';
  },

  // Pornhub: one details form per video in the upload queue, in the order the clips were attached.
  async 'Pornhub Shorts'(options, clips) {
    const titles = [...document.querySelectorAll('input[name=title]')];
    if (!titles.length) return 'Add the video first; its details form appears below the upload queue.';
    const tagBoxes = [...document.querySelectorAll('input[name=tags]')];
    const categoryBoxes = [...document.querySelectorAll('input[name=category]')];
    const notes = [];
    const missingCategories = new Set();
    for (const [i, title] of titles.entries()) {
      const clip = clips[i] || clips[clips.length - 1] || {};
      if (clip.caption) type(title, clip.caption);
      if (options.who) {
        const label = [...document.querySelectorAll('label')].filter(shown).filter(l => same(l.textContent, options.who))[i];
        if (label) label.click();
      }
      if (tagBoxes[i]) for (const tag of list(options.tags)) await pick(tagBoxes[i], tag, 'li', true);  // typed tags are fine
      for (const category of list(options.categories)) {
        if (categoryBoxes[i] && !await pick(categoryBoxes[i], category, 'li', false)) missingCategories.add(category);
      }
      if (categoryBoxes[i]) type(categoryBoxes[i], '');
    }
    notes.push(`Filled ${titles.length} video${titles.length === 1 ? '' : 's'}: title, tags and categories.`);
    if (missingCategories.size) notes.push(`Not in Pornhub's category list: ${[...missingCategories].join(', ')}.`);
    if (list(options.tags).length < 2) notes.push('Pornhub needs at least 2 tags.');
    notes.push('Set "When will this be published?" and visibility, tick the certification boxes yourself, then Submit for Review.');
    return notes.join(' ');
  },
};
