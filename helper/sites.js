// Planner platform name -> site host. Keep in step with content_scripts.matches in manifest.json.
// Instagram, TikTok and Reddit are left out on purpose: their terms forbid unapproved automation.
const HOSTS = {'Fansly': 'fansly.com', 'RedGifs': 'redgifs.com', 'Pornhub Shorts': 'pornhub.com'};

function platformFor(url) {
  try {
    const host = new URL(url).hostname;
    return Object.keys(HOSTS).find(name => host === HOSTS[name] || host.endsWith('.' + HOSTS[name])) || null;
  } catch (e) {
    return null;
  }
}
