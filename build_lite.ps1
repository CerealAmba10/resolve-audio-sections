$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m PyInstaller --noconfirm --windowed --onefile --exclude-module numpy --name 'Audio Sections Setup' --add-data 'native_bridge.lua;.' --add-data 'integration/Audio Sections High.xml;integration' plugin_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Compact installer build failed.' }
New-Item -ItemType Directory -Path dist/AudioSectionsLite -Force | Out-Null
Copy-Item -LiteralPath 'dist/Audio Sections Setup.exe' -Destination dist/AudioSectionsLite -Force
Copy-Item -LiteralPath QUICK-START-LITE.txt -Destination dist/AudioSectionsLite/QUICK-START.txt -Force
Copy-Item -LiteralPath README.md -Destination dist/AudioSectionsLite -Force
Compress-Archive -LiteralPath dist/AudioSectionsLite -DestinationPath dist/AudioSectionsLite-Windows.zip -Force
if ((Get-Item -LiteralPath dist/AudioSectionsLite-Windows.zip).Length -ge 20000000) { throw 'Compact package exceeds the 20 MB target.' }
