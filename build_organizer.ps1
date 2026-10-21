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
# Upload Layout Recorder, sent to the creator to map each site's post page.
New-Item -ItemType Directory -Path 'dist/Upload Layout Recorder' -Force | Out-Null
Remove-Item -LiteralPath 'dist/Upload Layout Recorder/recorder.zip', 'dist/Upload Layout Recorder/upload-layout-recorder.xpi' -ErrorAction SilentlyContinue
Compress-Archive -Path 'recorder/*' -DestinationPath 'dist/Upload Layout Recorder/recorder.zip'
Move-Item -LiteralPath 'dist/Upload Layout Recorder/recorder.zip' -Destination 'dist/Upload Layout Recorder/upload-layout-recorder.xpi'
Copy-Item -LiteralPath 'RECORDER-HOW-TO.txt' -Destination 'dist/Upload Layout Recorder/HOW TO USE.txt' -Force
Remove-Item -LiteralPath 'dist/Upload Layout Recorder.zip' -ErrorAction SilentlyContinue
Compress-Archive -Path 'dist/Upload Layout Recorder' -DestinationPath 'dist/Upload Layout Recorder.zip'
Write-Host "Built dist/DuckysClipOrganizer.exe, dist/Post Helper/post-helper.xpi and dist/Upload Layout Recorder.zip"
Write-Host "To ship an update: raise VERSION in organizer.py, then run release_organizer.ps1."
