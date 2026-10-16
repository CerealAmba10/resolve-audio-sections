"""Find local media tools; dependency downloads happen only during installation."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

DOWNLOAD_URL = 'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip'
DOWNLOAD_SHA256 = '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
TOOLS = ('ffmpeg', 'ffprobe')


def validate_pair(paths):
    if not paths or not all(Path(paths[name]).is_file() for name in TOOLS):
        return False
    try:
        for name in TOOLS:
            result = subprocess.run([str(paths[name]), '-filters' if name == 'ffmpeg' else '-version'],
                capture_output=True, text=True, errors='replace', timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode or (name == 'ffmpeg' and not all(f in result.stdout for f in ('astats', 'ametadata', 'afade'))):
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_pair(home):
    home = Path(home)
    configured = home / 'ffmpeg-paths.json'
    if configured.is_file():
        try:
            pair = json.loads(configured.read_text(encoding='utf-8'))
            if validate_pair(pair):
                return pair
        except (OSError, ValueError, KeyError, TypeError):
            pass
    candidates = [home / 'tools', home / '_internal/tools']
    on_path = {name: shutil.which(name) for name in TOOLS}
    if all(on_path.values()) and validate_pair(on_path):
        return on_path
    for name in TOOLS:
        if on_path[name]:
            candidates.append(Path(on_path[name]).parent)
    candidates += [Path('C:/ffmpeg/bin'), Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'ffmpeg/bin']
    packages = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Microsoft/WinGet/Packages'
    if packages.is_dir():
        candidates += list(packages.glob('Gyan.FFmpeg*/*/bin'))
    for folder in dict.fromkeys(candidates):
        pair = {name: str(folder / (name + '.exe')) for name in TOOLS}
        if validate_pair(pair):
            return pair
    return None


def install_archive(archive, home, expected_hash=DOWNLOAD_SHA256):
    archive, home = Path(archive), Path(home)
    digest = hashlib.sha256()
    with archive.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected_hash:
        raise ValueError('FFmpeg download did not pass verification. Please try installing again.')
    target = home / 'tools'
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        for name in (*TOOLS, 'LICENSE', 'README.txt'):
            suffix = '/bin/' + name + '.exe' if name in TOOLS else '/' + name
            matches = [item for item in bundle.infolist() if item.filename.endswith(suffix)]
            if len(matches) != 1:
                raise ValueError('FFmpeg download is missing required files.')
            destination = target / (name + '.exe' if name in TOOLS else name)
            # Copy only named files, never extract arbitrary archive paths.
            with bundle.open(matches[0]) as source, destination.open('wb') as output:
                shutil.copyfileobj(source, output)
    pair = {name: str(target / (name + '.exe')) for name in TOOLS}
    if not validate_pair(pair):
        raise ValueError('Downloaded FFmpeg could not run on this computer.')
    return pair


def ensure_tools(home, progress=lambda message: None):
    home = Path(home)
    progress('Checking for FFmpeg already installed on this computer…')
    pair = find_pair(home)
    if not pair:
        # HTTPS + pinned release hash. Never download or update during media scans.
        progress('Downloading FFmpeg once. Internet is needed for this setup step…')
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'ffmpeg.zip'
            download_archive(archive)
            progress('Verifying and installing FFmpeg…')
            pair = install_archive(archive, home)
    (home / 'ffmpeg-paths.json').write_text(json.dumps(pair, indent=2), encoding='utf-8')
    return pair


def download_archive(destination):
    # Windows' HTTPS trust store also handles managed PCs' certificate chains.
    # Keep certificate verification enabled; do not fall back to insecure TLS.
    quoted_path = "'" + str(Path(destination).resolve()).replace("'", "''") + "'"
    command = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
    command += "Invoke-WebRequest -UseBasicParsing -Uri '" + DOWNLOAD_URL + "' -OutFile " + quoted_path
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
        '-Command', command], capture_output=True, text=True, errors='replace', timeout=1800,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('FFmpeg could not be downloaded. Check your internet connection and try installing again.')


def tool_path(name, resource_root):
    bundled = Path(resource_root) / 'tools' / (name + '.exe')
    if bundled.is_file():
        return bundled
    home = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(resource_root)
    config = home / 'ffmpeg-paths.json'
    if config.is_file():
        try:
            path = Path(json.loads(config.read_text(encoding='utf-8'))[name])
            if path.is_file():
                return path
        except (OSError, ValueError, KeyError, TypeError):
            pass
    available = shutil.which(name)
    if available:
        return Path(available)
    raise ValueError('FFmpeg is unavailable. Run the Audio Sections installer again.')
