# print_exe_hash.ps1
#
# Prints the SHA-256 hash of the bundled installer. Run this AFTER
# build_windows.bat finishes — the value here is what users will see
# when they Get-FileHash the file they downloaded, so any mismatch
# means a tampered binary.
#
# Usage:
#     .\print_exe_hash.ps1
#     .\print_exe_hash.ps1 -Path "C:\path\to\Bullseye-Setup.exe"
#
# What we do with the output:
#   1. Paste the hash into the GitHub release notes for the version
#      we're shipping (so users can compare against an authoritative
#      source they didn't get from the same place as the binary).
#   2. Optionally also into the "verify before you run" card on the
#      download page if we want to make it copy-pasteable. (Currently
#      we just point users at the GitHub releases page and let them
#      compare there — keeps the landing page from needing a deploy
#      every release.)
#
# This script intentionally has no dependencies beyond stock
# PowerShell so the release-cutter can run it on any Windows box
# without setting up a venv.

[CmdletBinding()]
param(
    [string]$Path
)

# Default search order — pick the most recently modified Bullseye-
# Setup.exe in the usual places. Lets you run this script from
# anywhere with no args after a fresh build.
$DefaultCandidates = @(
    "$PSScriptRoot\dist\Bullseye-Setup.exe",
    "$PSScriptRoot\..\..\landing\public\Bullseye-Setup.exe",
    "$PSScriptRoot\Bullseye-Setup.exe"
)

if (-not $Path) {
    foreach ($candidate in $DefaultCandidates) {
        $resolved = (Resolve-Path -ErrorAction SilentlyContinue $candidate)
        if ($resolved -and (Test-Path $resolved.Path)) {
            $Path = $resolved.Path
            break
        }
    }
}

if (-not $Path -or -not (Test-Path $Path)) {
    Write-Error "Bullseye-Setup.exe not found. Build first or pass -Path."
    Write-Host "Tried:" -ForegroundColor Yellow
    $DefaultCandidates | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    exit 1
}

$Hash = Get-FileHash -Path $Path -Algorithm SHA256
$SizeMB = [math]::Round((Get-Item $Path).Length / 1MB, 2)

Write-Host ""
Write-Host "Bullseye installer hash" -ForegroundColor Cyan
Write-Host "------------------------" -ForegroundColor Cyan
Write-Host "Path     : $Path"
Write-Host "Size     : $SizeMB MB"
Write-Host "SHA-256  : $($Hash.Hash.ToLower())" -ForegroundColor Green
Write-Host ""
Write-Host "Paste-ready line for GitHub release notes:"
Write-Host ""
$tick = [char]0x60
$line = "    SHA-256: " + $tick + $Hash.Hash.ToLower() + $tick
Write-Host $line
Write-Host ""
