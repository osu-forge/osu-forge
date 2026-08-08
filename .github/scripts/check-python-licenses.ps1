<#
.SYNOPSIS
    Fails if any installed Python dependency has a license outside the allowlist.

.DESCRIPTION
    osu-forge ships under Apache-2.0, so a copyleft dependency anywhere in the
    tree would relicense the project. See CONTRIBUTING.md "Policy 1".

    This is an ALLOWLIST, not a denylist: anything not explicitly permitted
    fails. A package whose license cannot be determined fails too — absent a
    license, default copyright reserves all rights, which is stricter than GPL
    rather than looser.

    The guards below exist because the first version of this check reported
    "all licenses are on the allowlist" while pip-licenses had actually failed
    and returned nothing. A gate that collects zero data and passes is worse
    than no gate, so this script fails loudly on an empty or implausible
    result set instead of interpreting it as success.

.EXAMPLE
    pwsh .github/scripts/check-python-licenses.ps1
#>

[CmdletBinding()]
param(
    # Packages that must appear in the report. If pip-licenses silently returns
    # a truncated or wrong-environment listing, this catches it.
    [string[]] $Canary = @('numpy', 'scipy', 'statsmodels')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Both classifier-style names ("MIT License") and SPDX identifiers ("MIT")
# occur in the wild, often in the same report, so both forms are listed.
$Allowed = @(
    'Apache Software License', 'Apache-2.0', 'Apache 2.0', 'Apache License 2.0',
    'MIT License', 'MIT', 'MIT-0',
    'BSD License', 'BSD', 'BSD-2-Clause', 'BSD-3-Clause', '0BSD', 'BSD Zero Clause License',
    'ISC License (ISCL)', 'ISC',
    'Mozilla Public License 2.0 (MPL 2.0)', 'MPL-2.0',
    'Python Software Foundation License', 'PSF-2.0',
    'The Unlicense (Unlicense)', 'Unlicense',
    'zlib/libpng License', 'Zlib',
    'CC0 1.0 Universal (CC0 1.0) Public Domain Dedication', 'CC0-1.0',
    'Historical Permission Notice and Disclaimer (HPND)', 'HPND'
)

# Packages whose metadata does not declare a license even though the
# distribution ships one. Each entry records the verified license and where it
# was verified from, so the exception can be re-checked rather than trusted.
#
# This is deliberately per-package. Loosening the UNKNOWN rule globally would
# defeat the point of the gate.
$MetadataGapExceptions = @{
    'pycaw' = @{
        License = 'MIT'
        Reason  = 'Ships an MIT LICENSE file in <dist-info>/licenses/LICENSE but declares no License metadata or classifier. Verified 2026-08-08 against pycaw 20251023.'
    }
}

Write-Host 'Collecting installed package licenses...'
$raw = pip-licenses --format=json
if ($LASTEXITCODE -ne 0) {
    Write-Host "::error::pip-licenses exited with code $LASTEXITCODE. The gate cannot pass without a license report."
    exit 1
}

# Join and pass via -InputObject rather than piping. Windows PowerShell 5.1
# and PowerShell 7 unroll a piped array differently here, and the difference is
# silent: 5.1 can leave the whole result as a single element, which then reads
# as "1 package" and sails through the count check. Explicit text in, explicit
# object[] out, identical on both.
try {
    $jsonText = $raw -join [Environment]::NewLine
    $rows = [object[]](ConvertFrom-Json -InputObject $jsonText)
} catch {
    Write-Host "::error::Could not parse the pip-licenses output as JSON: $_"
    exit 1
}

# An empty result means the check did not run, not that everything is fine.
if ($rows.Count -eq 0) {
    Write-Host '::error::pip-licenses reported zero packages. Refusing to pass a gate that inspected nothing.'
    exit 1
}

$names = $rows | ForEach-Object { $_.Name.ToLowerInvariant() }
$missing = $Canary | Where-Object { $names -notcontains $_.ToLowerInvariant() }
if ($missing) {
    Write-Host "::error::Expected packages absent from the report: $($missing -join ', '). The wrong environment was probably inspected."
    exit 1
}

Write-Host "Inspecting $($rows.Count) packages."

# Compound expressions ("Apache-2.0 OR BSD-2-Clause", "A AND B", "A; B") are
# split and EVERY component must be allowed. For OR that is stricter than
# necessary — you may pick either branch — but a gate should fail closed, and
# a mixed permissive/copyleft OR is rare enough to handle by hand.
function Split-LicenseExpression {
    param([string] $Expression)
    $Expression -split '(?:;| AND | OR )' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

$violations = [System.Collections.Generic.List[string]]::new()
$excepted = [System.Collections.Generic.List[string]]::new()

foreach ($row in $rows) {
    $name = $row.Name
    $license = $row.License

    if ([string]::IsNullOrWhiteSpace($license) -or $license -eq 'UNKNOWN') {
        $exception = $MetadataGapExceptions[$name]
        if ($exception) {
            $excepted.Add(("  {0} {1} -> {2} (documented exception: {3})" -f $name, $row.Version, $exception.License, $exception.Reason))
            continue
        }
        $violations.Add(("  {0} {1} -> license could not be determined" -f $name, $row.Version))
        continue
    }

    $components = Split-LicenseExpression $license
    $disallowed = $components | Where-Object { $Allowed -notcontains $_ }
    if ($disallowed) {
        $violations.Add(("  {0} {1} -> {2}  (not allowed: {3})" -f $name, $row.Version, $license, ($disallowed -join ', ')))
    }
}

if ($excepted.Count -gt 0) {
    Write-Host ''
    Write-Host 'Documented metadata-gap exceptions:'
    $excepted | ForEach-Object { Write-Host $_ }
}

if ($violations.Count -gt 0) {
    Write-Host ''
    Write-Host 'Disallowed or undeterminable licenses:'
    $violations | ForEach-Object { Write-Host $_ }
    Write-Host ''
    Write-Host '::error::Dependency license policy violated. See CONTRIBUTING.md "Policy 1 - Dependency licenses".'
    exit 1
}

Write-Host ''
Write-Host "OK: all $($rows.Count) packages are on the allowlist."
