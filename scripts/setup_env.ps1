<#
.SYNOPSIS
    Set up the Python environment for the Wikicrop tools (Windows).

.DESCRIPTION
    Creates a virtual environment in .venv at the project root, installs the packages in
    scripts/requirements.txt into it, and reports which optional external programs were found.
    Safe to run again: an existing .venv is reused and its packages are brought up to date.

    Run from the project root:
        powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1
    Then, in each new terminal:
        .\.venv\Scripts\Activate.ps1
        python scripts\fetch_crop_pages.py --help

.PARAMETER Python
    Python interpreter to build the environment from. Default: the newest Python 3.11+ found
    through the py launcher, then python on PATH.

.PARAMETER Video
    Also install faster-whisper (requirements-video.txt) for video-transcript	ranscribe_video.py.

.PARAMETER Gpu
    With -Video: also install the CUDA 12 libraries (requirements-gpu.txt) to run it on an NVIDIA GPU.
#>
param([string]$Python = "", [switch]$Video, [switch]$Gpu)

$ErrorActionPreference = "Stop"
$Scripts = $PSScriptRoot
$Root = Split-Path -Parent $Scripts
$Venv = Join-Path $Root ".venv"

function Test-Python([string[]]$Cmd) {
    try {
        $v = & $Cmd[0] @($Cmd[1..($Cmd.Length)] | Where-Object { $_ }) -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]"3.11") { return $v }
    } catch {}
    return $null
}

# 1. Find a Python 3.11 or newer.
$candidates = @()
if ($Python) { $candidates += ,@($Python) }
foreach ($ver in "3.13", "3.12", "3.11") { $candidates += ,@("py", "-$ver") }
$candidates += ,@("python")
$py = $null
foreach ($c in $candidates) {
    $v = Test-Python $c
    if ($v) { $py = $c; Write-Host "Python $v found: $($c -join ' ')"; break }
}
if (-not $py) {
    Write-Error "No Python 3.11 or newer found. Install it from https://www.python.org/downloads/ and run this again."
}

# 2. Create or reuse the virtual environment.
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating $Venv"
    & $py[0] @($py[1..($py.Length)] | Where-Object { $_ }) -m venv $Venv
} else {
    Write-Host "Reusing $Venv"
}

# 3. Install the packages.
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install -r (Join-Path $Scripts "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Error "Package install failed (see the pip output above)." }
if ($Video) {
    & $VenvPy -m pip install -r (Join-Path $Scripts "requirements-video.txt")
    if ($LASTEXITCODE -ne 0) { Write-Error "faster-whisper install failed (see the pip output above)." }
    if ($Gpu) {
        & $VenvPy -m pip install -r (Join-Path $Scripts "requirements-gpu.txt")
        if ($LASTEXITCODE -ne 0) { Write-Error "CUDA library install failed (see the pip output above)." }
    }
} elseif ($Gpu) {
    Write-Host "-Gpu only applies together with -Video: skipped."
}

# 4. Optional external programs.
Write-Host ""
Write-Host "Optional programs:"
$lo = @("$env:ProgramFiles\LibreOffice\program\python.exe", "${env:ProgramFiles(x86)}\LibreOffice\program\python.exe") |
      Where-Object { Test-Path $_ } | Select-Object -First 1
if ($lo) { Write-Host "  LibreOffice  found  ($lo) - needed by document-export\odt_finish.py, run it with that python" }
else     { Write-Host "  LibreOffice  missing - only document-export\odt_finish.py (ODT contents and PDF export) needs it" }
foreach ($tool in @(@("pdftotext", "PDF text for source-finder\find_sources.py (Poppler)"),
                    @("curl", "fallback download in fetch_live_sheet.py"))) {
    $found = Get-Command $tool[0] -ErrorAction SilentlyContinue
    $state = if ($found) { "found  " } else { "missing" }
    Write-Host ("  {0,-12} {1} - {2}" -f $tool[0], $state, $tool[1])
}

Write-Host ""
Write-Host "Done. Activate the environment with:  .\.venv\Scripts\Activate.ps1"
Write-Host "Settings: copy scripts\settings.example.toml to scripts\settings.local.toml and fill it in (see scripts\project_config.py)."
