$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m PyInstaller --noconfirm --windowed --onedir --name AudioSectionsV2 --add-data 'queue_template.lua;.' --add-data 'README.md;.' --add-data 'integration/Audio Sections High.xml;integration' --add-binary 'tools/ffmpeg.exe;tools' --add-binary 'tools/ffprobe.exe;tools' app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Copy-Item -LiteralPath README.md -Destination dist/AudioSectionsV2/README.md
Copy-Item -LiteralPath tools/LICENSE -Destination dist/AudioSectionsV2/FFmpeg-LICENSE.txt
Copy-Item -LiteralPath tools/README.txt -Destination dist/AudioSectionsV2/FFmpeg-README.txt
Compress-Archive -LiteralPath dist/AudioSectionsV2 -DestinationPath dist/AudioSectionsV2-Windows-Offline.zip -Force
