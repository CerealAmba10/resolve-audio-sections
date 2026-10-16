"""One-time installation of the Resolve menu command and quiet local helper."""
import os
import queue
import shutil
import subprocess
import sys
import time
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from ffmpeg_tools import ensure_tools


def install(kit=None, progress=lambda message: None, single_file=False):
    kit = Path(kit or Path(sys.executable).parent)
    kit = Path(kit).resolve()
    home = (Path.home() / 'Documents/Audio Sections Plugin').resolve()
    source = kit / 'AudioSectionsWorker'
    if not single_file and not (source / 'AudioSectionsWorker.exe').is_file():
        raise ValueError('Unzip the entire Audio Sections package before installing.')
    home.mkdir(parents=True, exist_ok=True)
    # Resolve can keep using its existing helper if dependency setup fails.
    ensure_tools(home, progress)
    progress('Installing the Resolve menu command…')
    # Ask an earlier installed helper to exit before replacing its executable.
    stopped, shutdown = home / 'worker.stopped', home / 'shutdown.request'
    stopped.unlink(missing_ok=True)
    if (home / 'heartbeat.lua').exists() and time.time() - (home / 'heartbeat.lua').stat().st_mtime < 5:
        shutdown.touch()
        try:
            for _ in range(20):
                if stopped.exists():
                    time.sleep(0.5)
                    break
                time.sleep(0.25)
            else:
                raise ValueError('Audio Sections is busy. Let it finish sorting or rendering, then install again.')
        finally:
            shutdown.unlink(missing_ok=True)
    if single_file:
        executable = home / 'Audio Sections Setup.exe'
        if Path(sys.executable).resolve() != executable.resolve():
            # The stopped helper's process can hold its executable open for a few seconds.
            for attempt in range(40):
                try:
                    shutil.copy2(sys.executable, executable)
                    break
                except PermissionError:
                    if attempt == 39:
                        raise ValueError('The previous Audio Sections helper is still closing. Try installing again.')
                    time.sleep(0.25)
        template_path = Path(sys._MEIPASS) / 'native_bridge.lua'
        arguments = '--worker'
    else:
        shutil.copytree(source, home, dirs_exist_ok=True)
        executable = home / 'AudioSectionsWorker.exe'
        template_path = home / '_internal/native_bridge.lua'
        arguments = ''
    template = template_path.read_text(encoding='utf-8')
    literal = '"' + home.as_posix().replace('"', '\\"') + '"'
    script = template.replace('__HOME__', literal)
    scripts = Path(os.environ['APPDATA']) / 'Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility'
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / 'Audio Sections - Sort and Render.lua').unlink(missing_ok=True)  # earlier one-click command
    (scripts / 'Audio Sections.lua').write_text(script, encoding='utf-8')
    startup = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup/Audio Sections.lnk'
    startup.parent.mkdir(parents=True, exist_ok=True)
    def ps(value):
        return "'" + str(value).replace("'", "''") + "'"
    command = "$shell=New-Object -ComObject WScript.Shell; $link=$shell.CreateShortcut(" + ps(startup) + ");"
    command += '$link.TargetPath=' + ps(executable) + ';$link.Arguments=' + ps(arguments) + ';'
    command += '$link.WorkingDirectory=' + ps(home) + ';$link.WindowStyle=7;$link.Save()'
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                    '-Command', command], check=True, creationflags=subprocess.CREATE_NO_WINDOW,
                   capture_output=True)
    subprocess.Popen([str(executable)] + ([arguments] if arguments else []),
        env={**os.environ, 'PYINSTALLER_RESET_ENVIRONMENT': '1'},
        creationflags=subprocess.CREATE_NO_WINDOW)
    return home


def main(single_file=False):
    root = tk.Tk()
    root.title('Install Audio Sections')
    root.geometry('490x330')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Audio Sections', font=('Segoe UI', 22, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='One-click sorting and rendering in DaVinci Resolve.', wraplength=420).pack(anchor='w', pady=(10, 12))
    ttk.Label(frame, text='Reuses installed FFmpeg, or downloads it once if missing. After setup, sorting and rendering work offline.', wraplength=420).pack(anchor='w')
    status = tk.StringVar(value='Ready to install.')
    ttk.Label(frame, textvariable=status, wraplength=420).pack(anchor='w', pady=(14, 0))
    events = queue.Queue()
    def finish():
        while not events.empty():
            kind, value = events.get_nowait()
            if kind == 'progress':
                status.set(value)
            elif kind == 'done':
                messagebox.showinfo('Installed', 'In Resolve, open your timeline, then choose Workspace → Scripts → Audio Sections.')
                root.destroy()
                return
            else:
                button.configure(state='normal')
                status.set('Setup did not finish. You can try again.')
                messagebox.showerror('Could not install', value)
                return
        root.after(100, finish)
    def clicked():
        button.configure(state='disabled')
        def work():
            try:
                kit = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent / 'dist/AudioSectionsPlugin'
                install(kit, lambda message: events.put(('progress', message)), single_file)
                events.put(('done', None))
            except Exception as error:
                events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()
        root.after(100, finish)
    button = ttk.Button(frame, text='Install in DaVinci Resolve', command=clicked)
    button.pack(anchor='e', pady=(24, 0))
    root.mainloop()


if __name__ == '__main__':
    main()
