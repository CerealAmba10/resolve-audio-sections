// Planner platform name -> site host. Keep in step with content_scripts.matches in manifest.json.
// Instagram, TikTok and Reddit are left out on purpose: their terms forbid unapproved automation.
// Pornhub's creator uploader lives on pornhub.mainhub.com (only that host, not the rest of mainhub.com).
const HOSTS = {'Fansly': ['fansly.com'], 'RedGifs': ['redgifs.com'], 'Pornhub Shorts': ['pornhub.com', 'pornhub.mainhub.com']};

function platformFor(url) {
  try {
    const host = new URL(url).hostname;
    return Object.keys(HOSTS).find(name => HOSTS[name].some(h => host === h || host.endsWith('.' + h))) || null;
  } catch (e) {
    return null;
  }
}
