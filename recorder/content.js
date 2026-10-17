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
  const controls = () => [...document.querySelectorAll(SEL)]
    .filter(el => { const b = el.getBoundingClientRect(); return b.width && b.height || el.type === 'file'; })
    .slice(0, 400).map(describe);
  const send = (kind, el) => browser.runtime.sendMessage({kind, url: location.origin + location.pathname,
    title: document.title.slice(0, 80), target: el ? describe(el) : null, controls: controls(),
    sensitive: !!document.querySelector('input[type=password],input[type=email],input[autocomplete~=username],' +
      'input[autocomplete~=one-time-code],input[autocomplete~=current-password],input[inputmode=numeric][maxlength="6"]')});
  document.addEventListener('click', e => send('click', e.target.closest(SEL) || e.target), true);
  document.addEventListener('change', e => {
    const el = e.target;
    if (el.type === 'file') send(`file chosen (${el.files.length})`, el);
    else if (el.matches('select,[type=checkbox],[type=radio]')) send('change', el);
  }, true);
  setTimeout(() => send('page'), 1500);
})();
