// Runs on the listed sites only. Sends names of controls, never typed text.
(() => {
  const SEL = 'input,textarea,select,button,a[href],label,[role=button],[role=checkbox],[role=switch],[role=tab],' +
              '[role=option],[role=menuitem],[role=combobox],[role=radio],[contenteditable=true]';
  const label = el => {
    const typed = el.matches('input:not([type=button]):not([type=submit]),textarea,select') || el.isContentEditable;
    const own = el.labels && el.labels[0] ? el.labels[0].innerText : '';
    return (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || own ||
            (typed ? '' : el.innerText || el.value || '')).trim().replace(/\s+/g, ' ').slice(0, 80);
  };
  const path = el => {
    const parts = [];
    while (el && el.nodeType === 1 && parts.length < 6) {
      let step = el.tagName.toLowerCase();
      if (el.id) { parts.unshift(step + '#' + CSS.escape(el.id)); break; }
      const same = el.parentElement ? [...el.parentElement.children].filter(c => c.tagName === el.tagName) : [];
      if (same.length > 1) step += `:nth-of-type(${same.indexOf(el) + 1})`;
      parts.unshift(step);
      el = el.parentElement;
    }
    return parts.join(' > ');
  };
  const describe = el => {
    const box = el.getBoundingClientRect();
    return {tag: el.tagName.toLowerCase(), type: el.getAttribute('type') || '', role: el.getAttribute('role') || '',
      label: label(el), id: el.id || '', name: el.getAttribute('name') || '', testid: el.getAttribute('data-testid') || '',
      classes: typeof el.className === 'string' ? el.className.slice(0, 120) : '', path: path(el),
      box: [box.x, box.y, box.width, box.height].map(Math.round), accept: el.getAttribute('accept') || undefined,
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text).slice(0, 60) : undefined,
      checked: el.type === 'checkbox' || el.type === 'radio' ? el.checked : el.getAttribute('aria-checked') || undefined};
  };
  // Sites like Fansly build buttons from plain boxes with click handlers: count anything showing a pointer cursor
  // (but not the inner parts of something already counted) as a control too.
  const pointer = el => getComputedStyle(el).cursor === 'pointer';
  const clickable = () => {
    const found = new Set(document.querySelectorAll(SEL));
    for (const el of document.querySelectorAll('body *')) {
      if (found.size > 800) break;
      if (!found.has(el) && pointer(el) && !(el.parentElement && pointer(el.parentElement))) found.add(el);
    }
    return [...found];
  };
  const controls = () => clickable()
    .filter(el => { const b = el.getBoundingClientRect(); return b.width && b.height || el.type === 'file'; })
    .slice(0, 500).map(describe);
  const send = (kind, el, list = controls()) => browser.runtime.sendMessage({kind, url: location.origin + location.pathname,
    title: document.title.slice(0, 80), target: el ? describe(el) : null, controls: list,
    sensitive: !!document.querySelector('input[type=password],input[type=email],input[autocomplete~=username],' +
      'input[autocomplete~=one-time-code],input[autocomplete~=current-password],input[inputmode=numeric][maxlength="6"]')});
  const target = el => el.closest(SEL) || (() => {
    for (let node = el; node && node !== document.body; node = node.parentElement) if (pointer(node)) return node;
    return el;
  })();
  document.addEventListener('click', e => {
    const before = controls();
    send('click', target(e.target), before);
    // A moment later, record whatever the click opened (menus, panels, dialogs): only the controls that appeared.
    const seen = new Set(before.map(c => c.path + c.label));
    setTimeout(() => {
      const added = controls().filter(c => !seen.has(c.path + c.label));
      if (added.length) send('opened', null, added);
    }, 700);
  }, true);
  document.addEventListener('change', e => {
    const el = e.target;
    if (el.type === 'file') send(`file chosen (${el.files.length})`, el);
    else if (el.matches('select,[type=checkbox],[type=radio]')) send('change', el);
  }, true);
  setTimeout(() => send('page'), 1500);
})();
