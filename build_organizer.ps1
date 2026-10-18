$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; not building.' }
python -m PyInstaller --noconfirm --windowed --onefile --name DuckysClipOrganizer --icon ui/icon.ico --add-data 'ui;ui' organizer.py
if ($LASTEXITCODE -ne 0) { throw 'Clip Organizer build failed.' }
# Post Helper extension package (sign it on addons.mozilla.org before handing it out).
New-Item -ItemType Directory -Path 'dist/Post Helper' -Force | Out-Null
Remove-Item -LiteralPath 'dist/Post Helper/post-helper.zip', 'dist/Post Helper/post-helper.xpi' -ErrorAction SilentlyContinue
Compress-Archive -Path 'helper/*' -DestinationPath 'dist/Post Helper/post-helper.zip'
Move-Item -LiteralPath 'dist/Post Helper/post-helper.zip' -Destination 'dist/Post Helper/post-helper.xpi'
Copy-Item -LiteralPath 'HELPER-HOW-TO.txt' -Destination 'dist/Post Helper/HOW TO USE.txt' -Force
Copy-Item -LiteralPath 'HELPER-HOW-TO.txt' -Destination 'dist/HOW TO USE.txt' -Force
Write-Host "Built dist/DuckysClipOrganizer.exe and dist/Post Helper/post-helper.xpi"
Write-Host "To ship an update: bump VERSION in organizer.py, rebuild, then create a GitHub release tagged vX.Y.Z with DuckysClipOrganizer.exe attached."
