"""Test-only WebDriver BiDi client for driving a local Firefox with the standard library."""
import base64
import json
import os
import select
import socket
import subprocess
import time
from collections import deque
from pathlib import Path

PREFS = {'browser.aboutwelcome.enabled': False, 'browser.shell.checkDefaultBrowser': False,
         'startup.homepage_welcome_url': '', 'browser.startup.homepage_override.mstone': 'ignore',
         'datareporting.policy.dataSubmissionEnabled': False, 'toolkit.telemetry.reportingpolicy.firstRun': False}


class Bidi:
    """Minimal WebSocket + WebDriver BiDi client (RFC 6455 text frames; enough for a local browser)."""

    def __init__(self, port, timeout=60):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.create_connection(('127.0.0.1', port), timeout=5)
                key = base64.b64encode(os.urandom(16)).decode()
                self.sock.sendall((f'GET /session HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n'
                                   f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
                self.buffer = b''
                while b'\r\n\r\n' not in self.buffer:
                    self.buffer += self.read_some()
                head, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
                if b' 101 ' not in head.split(b'\r\n')[0]:
                    raise ConnectionError(head[:200].decode(errors='replace'))
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise ConnectionError('Firefox did not open its automation port.') from None
                time.sleep(0.5)
        self.sock.settimeout(None)
        self.events, self.next_id = deque(), 0

    def read_some(self):
        chunk = self.sock.recv(1 << 20)
        if not chunk:
            raise ConnectionError('browser closed')
        return chunk

    def read(self, n):
        while len(self.buffer) < n:
            self.buffer += self.read_some()
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

    def frame(self, opcode, data):
        mask = os.urandom(4)
        n = len(data)
        size = bytes([0x80 | n]) if n < 126 else bytes([0xFE]) + n.to_bytes(2, 'big') if n < 65536 else bytes([0xFF]) + n.to_bytes(8, 'big')
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(data))  # client frames are small commands
        self.sock.sendall(bytes([0x80 | opcode]) + size + mask + masked)

    def recv(self, timeout=None):
        """Next text message, or None if nothing arrives within timeout."""
        if timeout is not None and not self.buffer and not select.select([self.sock], [], [], timeout)[0]:
            return None
        message = b''
        while True:
            first, second = self.read(2)
            n = second & 0x7F
            if n >= 126:
                n = int.from_bytes(self.read(2 if n == 126 else 8), 'big')
            data, opcode = self.read(n), first & 0x0F
            if opcode == 8:
                raise ConnectionError('browser closed')
            if opcode == 9:
                self.frame(10, data)
            elif opcode in (0, 1, 2):
                message += data
                if first & 0x80:
                    return message.decode('utf-8')

    def call(self, method, **params):
        self.next_id += 1
        self.frame(1, json.dumps(dict(id=self.next_id, method=method, params=params)).encode())
        while True:
            message = json.loads(self.recv())
            if message.get('id') == self.next_id:
                if message.get('type') == 'error':
                    raise RuntimeError(f"{method}: {message.get('error')}: {message.get('message')}")
                return message['result']
            self.events.append(message)

    def event(self, timeout=None):
        if self.events:
            return self.events.popleft()
        text = self.recv(timeout)
        return json.loads(text) if text else None


def find_firefox():
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe') as key:
                path = winreg.QueryValue(key, None)
                if Path(path).is_file():
                    return path
        except OSError:
            pass
    for root in (os.environ.get('ProgramFiles', ''), os.environ.get('ProgramFiles(x86)', ''), os.environ.get('LOCALAPPDATA', '')):
        path = Path(root) / 'Mozilla Firefox' / 'firefox.exe'
        if root and path.is_file():
            return str(path)
    raise RuntimeError('Firefox was not found. Install Firefox, then run this again.')


def launch(profile, url='about:blank', headless=True, prefs=None):
    """Start a separate Firefox instance on a dedicated profile; return (process, Bidi)."""
    profile.mkdir(parents=True, exist_ok=True)
    (profile / 'user.js').write_text(''.join(f'user_pref({json.dumps(k)}, {json.dumps(v)});\n' for k, v in {**PREFS, **(prefs or {})}.items()),
                                     encoding='utf-8')
    with socket.socket() as probe_port:
        probe_port.bind(('127.0.0.1', 0))
        port = probe_port.getsockname()[1]
    args = [find_firefox(), '-profile', str(profile), '-no-remote', '-new-instance', '-remote-allow-system-access', '--remote-debugging-port', str(port)]
    process = subprocess.Popen(args + (['-headless'] if headless else []) + [url])
    for attempt in range(2):  # a brand-new profile's first start can drop the first connection
        try:
            bidi = Bidi(port)
            # 'ignore' leaves the site's own dialogs (e.g. "discard draft?") for the person to answer.
            bidi.call('session.new', capabilities={'alwaysMatch': {'unhandledPromptBehavior': 'ignore'}})
            return process, bidi
        except ConnectionError:
            if attempt or process.poll() is not None:
                raise
            time.sleep(2)
