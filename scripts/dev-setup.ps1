<#
.SYNOPSIS
    Set up a development environment in one command.

.DESCRIPTION
    osuforge reads beatmaps through a compiled Rust extension, which is not on
    PyPI and has to be built from this checkout. Getting that wrong leaves a
    stale .pyd answering with the old geometry, so this exists to make the whole
    sequence one step rather than five remembered ones.

    Contributors only. An end user should never see any of this — releases ship
    a signed installer with the extension already built.

.PARAMETER Oracle
    Also create the isolated environment for the differential oracle. It holds
    circleguard, which is AGPL-3.0 and is therefore never installed alongside
    anything this project ships. Without it the oracle tests skip and no offset
    recommendation is issued, which is intended rather than inconvenient.
#>
[CmdletBinding()]
param(
    [switch]$Oracle
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> repository: $root"

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    Write-Host '==> creating .venv (Python 3.14)'
    py -3.14 -m venv .venv
}
$python = Join-Path $root '.venv\Scripts\python.exe'
Write-Host ("==> " + (& $python --version))

Write-Host '==> installing build tooling'
& $python -m pip install --quiet --upgrade pip 'maturin>=1.7,<2'

Write-Host '==> building the diffcalc extension'
& $python -m maturin build -m diffcalc\pyo3\Cargo.toml --release --out dist
if ($LASTEXITCODE -ne 0) { throw 'maturin build failed' }

Write-Host '==> installing it'
& $python -m pip install --quiet --force-reinstall --no-index --find-links dist osu-forge-diffcalc

Write-Host '==> installing core in editable mode'
& $python -m pip install --quiet -e 'core[dev]'

if ($Oracle) {
    if (-not (Test-Path 'tools\oracle-venv\Scripts\python.exe')) {
        Write-Host '==> creating tools\oracle-venv (isolated, AGPL)'
        py -3.14 -m venv tools\oracle-venv
    }
    & 'tools\oracle-venv\Scripts\python.exe' -m pip install --quiet --upgrade pip circleguard
    Write-Host '==> circleguard installed in its own environment'
}

Write-Host ''
Write-Host 'Done. Checks that must pass:'
Write-Host '  .venv\Scripts\python -m ruff check . ; .venv\Scripts\python -m ruff format --check .'
Write-Host '  cd core; ..\.venv\Scripts\python -m mypy; ..\.venv\Scripts\python -m pytest'
Write-Host '  cargo fmt --all --check; cargo clippy --workspace --all-targets -- -D warnings'
Write-Host '  cargo test --workspace; cargo deny check licenses sources advisories bans'
Write-Host ''
Write-Host 'Rebuild the extension after changing anything under diffcalc\ — a stale'
Write-Host '.pyd will keep answering with the old geometry without complaining.'
