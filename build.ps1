$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m PyInstaller --noconfirm --windowed --onedir --name AudioSections --add-data 'queue_template.lua;.' --add-data 'README.md;.' --add-binary 'tools/ffmpeg.exe;tools' --add-binary 'tools/ffprobe.exe;tools' app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Copy-Item -LiteralPath README.md -Destination dist/AudioSections/README.md
Copy-Item -LiteralPath tools/LICENSE -Destination dist/AudioSections/FFmpeg-LICENSE.txt
Copy-Item -LiteralPath tools/README.txt -Destination dist/AudioSections/FFmpeg-README.txt
Compress-Archive -LiteralPath dist/AudioSections -DestinationPath dist/AudioSections-Windows-Offline.zip -Force
