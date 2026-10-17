const bg = browser.extension.getBackgroundPage();
const keep = new Set();

function render() {
  document.getElementById('summary').textContent =
    `${bg.steps.length} steps and ${bg.network.length} site requests recorded.`;
  const grid = document.getElementById('shots');
  grid.textContent = '';
  for (const step of bg.steps.filter(s => s.shot)) {
    keep.add(step.n);
    const card = grid.appendChild(document.createElement('label'));
    const box = Object.assign(card.appendChild(document.createElement('input')), {type: 'checkbox', checked: true});
    box.onchange = () => box.checked ? keep.add(step.n) : keep.delete(step.n);
    card.appendChild(document.createTextNode(` ${step.n}. ${step.kind} ${step.target ? step.target.label : ''}`));
    card.appendChild(Object.assign(document.createElement('img'), {src: step.shot}));
  }
}

document.getElementById('save').onclick = async () => {
  const text = s => new TextEncoder().encode(s);
  const files = [];
  const events = bg.steps.map(({shot, ...step}) => {
    if (shot && keep.has(step.n)) {
      step.shot = `${String(step.n).padStart(4, '0')}.png`;
      files.push({name: 'shots/' + step.shot, data: Uint8Array.from(atob(shot.split(',')[1]), c => c.charCodeAt(0))});
    }
    return JSON.stringify(step);
  });
  files.unshift({name: 'events.jsonl', data: text(events.join('\n') + '\n')},
                {name: 'network.jsonl', data: text(bg.network.map(r => JSON.stringify(r)).join('\n') + '\n')});
  const local = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
  const stamp = local.toISOString().slice(0, 16).replace(/[:T]/g, '-');
  const url = URL.createObjectURL(makeZip(files));
  await browser.downloads.download({url, filename: `layout recording ${stamp}.zip`, saveAs: false});
  document.getElementById('summary').textContent = 'Saved to your Downloads folder. Send that zip back.';
};

document.getElementById('clear').onclick = () => {
  bg.steps.length = 0;
  bg.network.length = 0;
  bg.badge();
  render();
};

render();
