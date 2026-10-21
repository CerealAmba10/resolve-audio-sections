# Publishes a Ducky's Clip Organizer update that every copy picks up with "Check for updates".
#   1. Raise VERSION in organizer.py (e.g. 1.0.0 -> 1.1.0). Copies only update to a HIGHER version.
#   2. Run:  powershell -ExecutionPolicy Bypass -File release_organizer.ps1 "What changed, in a sentence or two"
# It runs the tests, builds the exe and creates the GitHub release "organizer-vX.Y.Z" with the exe attached
# (GitHub records its SHA-256, which the app checks before installing). Needs the GitHub CLI, signed in (gh auth login).
param([string]$Notes = 'Bug fixes and improvements.')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$version = (Select-String -Path organizer.py -Pattern "^VERSION = '([\d.]+)'").Matches[0].Groups[1].Value
$tag = "organizer-v$version"
$ErrorActionPreference = 'Continue'  # "release not found" is the expected answer here, not an error
gh release view $tag -R CerealAmba10/resolve-audio-sections 2>&1 | Out-Null
$exists = $LASTEXITCODE -eq 0
$ErrorActionPreference = 'Stop'
if ($exists) { throw "$tag is already released. Raise VERSION in organizer.py first." }
powershell -NoProfile -ExecutionPolicy Bypass -File build_organizer.ps1
if ($LASTEXITCODE -ne 0) { throw 'Build failed; nothing was released.' }
# The release's tag marks the commit it was built from; that commit must already be pushed to GitHub.
$commit = git rev-parse HEAD
if (-not (git branch -r --contains $commit)) { throw 'Push this commit to GitHub first (git push origin <branch>).' }
gh release create $tag 'dist/DuckysClipOrganizer.exe' -R CerealAmba10/resolve-audio-sections --target $commit `
    --title "Ducky's Clip Organizer $version" --notes $Notes
if ($LASTEXITCODE -ne 0) { throw 'Creating the GitHub release failed.' }
Write-Host "Released $tag. Copies older than $version will offer it under Check for updates."
