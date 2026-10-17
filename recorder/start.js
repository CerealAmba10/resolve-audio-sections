for (const [name, url] of SITES) {
  const link = Object.assign(document.createElement('a'), {href: url, target: '_blank', textContent: name});
  document.getElementById('sites').appendChild(document.createElement('li')).appendChild(link);
}
