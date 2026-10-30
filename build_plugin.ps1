$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m PyInstaller --noconfirm --windowed --onedir --exclude-module numpy --name AudioSectionsWorker --add-data 'native_bridge.lua;.' --add-data 'integration/Audio Sections High.xml;integration' --add-binary 'tools/ffmpeg.exe;tools' --add-binary 'tools/ffprobe.exe;tools' worker.py
if ($LASTEXITCODE -ne 0) { throw 'Worker build failed.' }
python -m PyInstaller --noconfirm --windowed --onefile --name 'Install Audio Sections' setup_plugin.py
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
New-Item -ItemType Directory -Path dist/AudioSectionsPlugin -Force | Out-Null
Copy-Item -LiteralPath dist/AudioSectionsWorker -Destination dist/AudioSectionsPlugin -Recurse -Force
Copy-Item -LiteralPath 'dist/Install Audio Sections.exe' -Destination dist/AudioSectionsPlugin -Force
Copy-Item -LiteralPath README.md -Destination dist/AudioSectionsPlugin/README.md -Force
Copy-Item -LiteralPath QUICK-START.txt -Destination dist/AudioSectionsPlugin/QUICK-START.txt -Force
Copy-Item -LiteralPath VALIDATION.md -Destination dist/AudioSectionsPlugin/VALIDATION.md -Force
Copy-Item -LiteralPath tools/LICENSE -Destination dist/AudioSectionsPlugin/FFmpeg-LICENSE.txt -Force
Copy-Item -LiteralPath tools/README.txt -Destination dist/AudioSectionsPlugin/FFmpeg-README.txt -Force
Compress-Archive -LiteralPath dist/AudioSectionsPlugin -DestinationPath dist/AudioSectionsPlugin-Windows-Offline.zip -Force
