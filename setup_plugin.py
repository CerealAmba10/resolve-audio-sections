"""One-time installation of the Resolve menu command and quiet local helper."""
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


def install(kit):
    kit = Path(kit).resolve()
    home = (Path.home() / 'Documents/Audio Sections Plugin').resolve()
    source = kit / 'AudioSectionsWorker'
    if not (source / 'AudioSectionsWorker.exe').is_file():
        raise ValueError('Unzip the entire Audio Sections package before installing.')
    home.mkdir(parents=True, exist_ok=True)
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
    shutil.copytree(source, home, dirs_exist_ok=True)
    template = (home / '_internal/native_bridge.lua').read_text(encoding='utf-8')
    literal = '"' + home.as_posix().replace('"', '\\"') + '"'
    script = template.replace('__HOME__', literal)
    scripts = Path(os.environ['APPDATA']) / 'Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts/Utility'
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / 'Audio Sections - Sort and Render.lua').write_text(script, encoding='utf-8')
    startup = Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/Startup/Audio Sections.lnk'
    startup.parent.mkdir(parents=True, exist_ok=True)
    def ps(value):
        return "'" + str(value).replace("'", "''") + "'"
    command = "$shell=New-Object -ComObject WScript.Shell; $link=$shell.CreateShortcut(" + ps(startup) + ");"
    command += '$link.TargetPath=' + ps(home / 'AudioSectionsWorker.exe') + ';'
    command += '$link.WorkingDirectory=' + ps(home) + ';$link.WindowStyle=7;$link.Save()'
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
                    '-Command', command], check=True, creationflags=subprocess.CREATE_NO_WINDOW,
                   capture_output=True)
    subprocess.Popen([str(home / 'AudioSectionsWorker.exe')], creationflags=subprocess.CREATE_NO_WINDOW)
    return home


def main():
    root = tk.Tk()
    root.title('Install Audio Sections')
    root.geometry('490x270')
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=28)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Audio Sections', font=('Segoe UI', 22, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='One-click sorting and rendering in DaVinci Resolve.', wraplength=420).pack(anchor='w', pady=(10, 12))
    ttk.Label(frame, text='Installs a Resolve menu command and a quiet local helper that starts when you sign in. Everything stays offline.', wraplength=420).pack(anchor='w')
    def clicked():
        button.configure(state='disabled')
        root.update()
        try:
            kit = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent / 'dist/AudioSectionsPlugin'
            install(kit)
            messagebox.showinfo('Installed', 'In Resolve, open Workspace → Scripts → Audio Sections - Sort and Render.\n\nSelect your recording and click that command. No Console or companion controls are needed.')
            root.destroy()
        except Exception as error:
            button.configure(state='normal')
            messagebox.showerror('Could not install', str(error))
    button = ttk.Button(frame, text='Install in DaVinci Resolve', command=clicked)
    button.pack(anchor='e', pady=(24, 0))
    root.mainloop()


if __name__ == '__main__':
    main()
