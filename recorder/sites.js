// The only sites recorded. Keep in step with content_scripts.matches in manifest.json.
// Instagram, TikTok and Reddit forbid unapproved automation, so they use native schedulers or official APIs instead.
const SITES = [
  ['Fansly', 'https://fansly.com/'], ['RedGifs', 'https://www.redgifs.com/'], ['Pornhub', 'https://www.pornhub.com/'],
];
const HOSTS = ['fansly.com', 'redgifs.com', 'pornhub.com'];

// webRequest match patterns for the sites above (an IP address can't take a "*." prefix).
const SITE_URLS = HOSTS.map(h => /^[\d.]+$/.test(h) ? `*://${h}/*` : `*://*.${h}/*`);

function allowed(url) {
  try {
    const host = new URL(url).hostname;
    return HOSTS.some(h => host === h || host.endsWith('.' + h));
  } catch (e) {
    return false;
  }
}
