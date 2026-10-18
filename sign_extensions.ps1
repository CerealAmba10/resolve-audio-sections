# Signs the Firefox add-ons with Mozilla (free, "unlisted": they never appear on the public add-on store).
# Signed .xpi files install with a normal double-click and survive Firefox restarts.
#
# One-time setup:
#   1. Sign in at https://addons.mozilla.org (make a free account if needed).
#   2. Open https://addons.mozilla.org/developers/addon/api/key/ and generate credentials.
#   3. In this PowerShell window, paste your own values (they are not saved anywhere):
#        $env:WEB_EXT_API_KEY = 'user:12345:67'
#        $env:WEB_EXT_API_SECRET = 'your-secret'
# Then run:  powershell -ExecutionPolicy Bypass -File sign_extensions.ps1            (both add-ons)
#        or: powershell -ExecutionPolicy Bypass -File sign_extensions.ps1 recorder   (just one)
# Each upload needs a new "version" in that add-on's manifest.json; bump it before signing again.
param([string[]]$Which = @('recorder', 'helper'))
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not $env:WEB_EXT_API_KEY -or -not $env:WEB_EXT_API_SECRET) {
    Write-Host 'Set $env:WEB_EXT_API_KEY and $env:WEB_EXT_API_SECRET first (see the top of this script).'
    exit 1
}
New-Item -ItemType Directory -Path 'dist/signed' -Force | Out-Null
foreach ($dir in $Which) {
    Write-Host "Signing $dir (Mozilla's automatic review usually takes a few minutes)..."
    npx --yes web-ext@8 sign --source-dir $dir --artifacts-dir 'dist/signed' --channel unlisted
    if ($LASTEXITCODE -ne 0) { throw "Signing $dir failed; the message above says why." }
}
Write-Host 'Signed add-ons are in dist/signed. Send those .xpi files: they install by opening them in Firefox.'
