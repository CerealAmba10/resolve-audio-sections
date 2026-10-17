# Native tools write progress to stderr; each step's exit code is checked instead of ErrorActionPreference.
Set-Location -LiteralPath $PSScriptRoot
# Publish the version in updater.py as a GitHub release; installed helpers offer it as an update.
$version = (Select-String -LiteralPath updater.py -Pattern "^VERSION = '([\d.]+)'").Matches[0].Groups[1].Value
if (-not $version) { throw 'VERSION not found in updater.py.' }
if (git status --porcelain --untracked-files=no) { throw 'Commit your changes before releasing.' }
gh release view "v$version" *> $null
if ($LASTEXITCODE -eq 0) { throw "v$version is already released. Bump VERSION in updater.py." }
python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
powershell -NoProfile -ExecutionPolicy Bypass -File build_lite.ps1
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
Copy-Item -LiteralPath 'dist/Audio Sections Setup.exe' -Destination dist/AudioSectionsSetup.exe -Force -ErrorAction Stop
git push origin HEAD:main
if ($LASTEXITCODE -ne 0) { throw 'Push failed.' }
gh release create "v$version" dist/AudioSectionsSetup.exe dist/AudioSectionsLite-Windows.zip --target (git rev-parse HEAD) --title "Audio Sections $version" --generate-notes
if ($LASTEXITCODE -ne 0) { throw 'Release failed.' }
