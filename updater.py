"""Updates from GitHub Releases, so people get new builds without being sent an exe.

The latest release of REPO carries ASSET (the setup exe). Its tag ("v1.2") is compared with VERSION; a newer
build is downloaded, checked against the SHA-256 digest GitHub publishes for the asset (no digest or a mismatch:
refused), then run with --install-only. The installer stops this helper, replaces it and starts the new one.
Standard library only.
"""
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = 'CerealAmba10/resolve-audio-sections'
VERSION = '1.8'          # bump for every published build; the tag is "v" + this
ASSET = 'AudioSectionsSetup.exe'
MAX_BYTES = 200 * 1024 * 1024


def parse_version(text):
    """'v1.2' / '1.2' -> (1, 2); None if it isn't a plain dotted version."""
    match = re.fullmatch(r'v?(\d+(?:\.\d+){0,3})', (text or '').strip())
    return tuple(int(p) for p in match.group(1).split('.')) if match else None


def _get(url, timeout=8):
    request = urllib.request.Request(url, headers={'User-Agent': 'AudioSections-updater',
                                                   'Accept': 'application/vnd.github+json'})
    return urllib.request.urlopen(request, timeout=timeout)


def latest(fetch=None):
    """Newest release as {"version", "url", "sha256"} if it is newer than this build and complete, else None."""
    fetch = fetch or (lambda: json.load(_get(f'https://api.github.com/repos/{REPO}/releases/latest')))
    release = fetch()
    if not isinstance(release, dict) or release.get('draft') or release.get('prerelease'):
        return None
    version = parse_version(release.get('tag_name'))
    if not version or version <= parse_version(VERSION):
        return None
    for asset in release.get('assets') or []:
        digest = asset.get('digest') or ''
        if asset.get('name') == ASSET and re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
            url = asset.get('browser_download_url') or ''
            if url.startswith(f'https://github.com/{REPO}/releases/download/'):
                return {'version': '.'.join(map(str, version)), 'url': url, 'sha256': digest[7:]}
    return None


def download(url, target, sha256, opener=None):
    """Stream url to target; keep it only if the bytes match sha256."""
    opener = opener or (lambda u: _get(u, timeout=30))
    digest, size = hashlib.sha256(), 0
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + '.part')
    try:
        with opener(url) as source, open(partial, 'wb') as out:
            while True:
                chunk = source.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('The update is larger than expected.')
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != sha256:
            raise ValueError('The downloaded update does not match its published SHA-256.')
        partial.replace(target)
    finally:
        if partial.exists():
            partial.unlink()


def apply(update, home):
    """Download and verify the new setup, then let it reinstall (it stops this idle helper)."""
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Updates only work in the installed Audio Sections.')
    setup = Path(home) / 'update' / ASSET
    download(update['url'], setup, update['sha256'])
    subprocess.Popen([str(setup), '--install-only'], close_fds=True, creationflags=0x00000008)  # DETACHED_PROCESS


if __name__ == '__main__':
    good = 'sha256:' + 'a' * 64
    url = f'https://github.com/{REPO}/releases/download/v9.0/{ASSET}'
    release = dict(tag_name='v9.0', assets=[dict(name=ASSET, digest=good, browser_download_url=url)])
    assert latest(lambda: release) == {'version': '9.0', 'url': url, 'sha256': 'a' * 64}
    assert latest(lambda: dict(release, tag_name='v' + VERSION)) is None                  # not newer
    assert latest(lambda: dict(release, assets=[dict(release['assets'][0], digest='')])) is None   # no digest
    assert latest(lambda: dict(release, assets=[dict(release['assets'][0],
                                                     browser_download_url='https://evil.example/x.exe')])) is None
    print('updater ok')
