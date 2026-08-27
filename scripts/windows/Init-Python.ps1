# Windows-хелперы Python/venv. Без param() скрипта, без работы при dotsource.
# Ритуал холодного старта остаётся в оркестраторе Agent-Init.ps1.

# ============================================
# Robust Python discovery (critical when PATH is polluted by Inkscape, Git, etc.)
# ============================================

function Find-ReliablePython {
    [CmdletBinding()]
    param()

    # Prefer existing project .venv (critical for repeated runs on polluted PATH machines)
    $projectVenv = ".\.venv\Scripts\python.exe"
    if (Test-Path $projectVenv) {
        try {
            $ver = & $projectVenv --version 2>&1
            if ($ver -match "Python 3\.(1[0-9]|[2-9][0-9])") {
                Write-Host "  Preferring existing project .venv python: $projectVenv" -ForegroundColor Green
                return (Resolve-Path $projectVenv).Path
            }
        } catch {}
    }

    $badSubstrings = @(
        'inkscape',
        'git\mingw',
        'git\usr\bin',
        'msys64',
        'windowsapps',
        'windows defender',
        'program files (x86)\microsoft visual studio'
    )

    $candidates = [System.Collections.Generic.List[string]]::new()

    # 1. Windows Python Launcher "py" (highest priority - designed exactly for this situation)
    $pyVersions = @('-3.12', '-3.11', '-3.10', '-3.9', '')
    foreach ($ver in $pyVersions) {
        try {
            $exe = & py $ver -c "import sys; print(sys.executable)" 2>$null
            if ($exe -and (Test-Path $exe -ErrorAction SilentlyContinue)) {
                $candidates.Add([string]$exe)
            }
        } catch {}
    }

    # 2. Explicit well-known locations (bypass PATH completely)
    $userProfile   = $env:USERPROFILE
    $programFiles  = ${env:ProgramFiles}
    $localAppData  = $env:LOCALAPPDATA

    $searchRoots = @(
        "C:\Python312", "C:\Python311", "C:\Python310", "C:\Python39", "C:\Python38",
        (Join-Path $programFiles  "Python312"),
        (Join-Path $programFiles  "Python311"),
        (Join-Path $programFiles  "Python310"),
        (Join-Path $localAppData  "Programs\Python\Python312"),
        (Join-Path $localAppData  "Programs\Python\Python311"),
        (Join-Path $localAppData  "Programs\Python\Python310"),
        (Join-Path $userProfile   "AppData\Local\Programs\Python\Python312"),
        (Join-Path $userProfile   "AppData\Local\Programs\Python\Python311"),
        (Join-Path $userProfile   "AppData\Local\Programs\Python\Python310")
    ) | Where-Object { $_ -and (Test-Path $_) }

    foreach ($root in $searchRoots) {
        $exe = Join-Path $root "python.exe"
        if (Test-Path $exe) { $candidates.Add($exe) }
    }

    # 3. Walk PATH but we will filter aggressively later
    $pathDirs = ($env:PATH -split ';') | Where-Object { $_.Trim() }
    foreach ($dir in $pathDirs) {
        $exe = Join-Path $dir "python.exe"
        if (Test-Path $exe) { $candidates.Add($exe) }
    }

    # Deduplicate + filter bad + validate
    $seen = @{}
    foreach ($exe in $candidates) {
        $lower = $exe.ToLowerInvariant()
        if ($seen.ContainsKey($lower)) { continue }
        $seen[$lower] = $true

        $isBad = $false
        foreach ($bad in $badSubstrings) {
            if ($lower -like "*$bad*") { $isBad = $true; break }
        }
        if ($isBad) { continue }

        try {
            $ver = & $exe --version 2>&1
            if ($ver -match "Python 3\.(1[0-9]|[2-9][0-9])") {
                # Must be able to create venvs
                $venvTest = & $exe -c "import venv; print('venv_ok')" 2>&1
                if ($venvTest -match "venv_ok") {
                    return $exe
                }
            }
        } catch {}
    }

    return $null
}

# ============================================
# Safe Python environment discovery helpers (Windows-specific)
# These exist to prevent the very common class of errors where the agent
# guesses long site-packages paths, mixes cmd.exe syntax (%VAR%, findstr),
# or uses wrong Python (MS Store / Inkscape / Git Bash Python).
# ============================================

function Get-PythonEnvironmentReport {
    <#
    .SYNOPSIS
        Safely reports the real Python environment without guessing paths.
        Designed to be called by the agent via the 'powershell' tool when it needs
        to investigate editable installs, site-packages location, etc.

    .DESCRIPTION
        Uses the venv python.exe directly (never bare 'python').
        Prints structured, copy-paste friendly output.
        Strongly preferred over manual 'dir ... findstr' or '%LOCALAPPDATA%...' tricks.
    #>
    param(
        [Parameter(Mandatory=$true)]
        [string]$PythonExe
    )

    if (-not (Test-Path $PythonExe)) {
        Write-Warning "Python executable not found: $PythonExe"
        return
    }

    Write-Host ""
    Write-Host "=== SAFE PYTHON ENVIRONMENT REPORT (use this instead of guessing paths) ===" -ForegroundColor Cyan
    Write-Host "Source: $PythonExe" -ForegroundColor DarkGray
    Write-Host ""

    & $PythonExe -c @"
import sys, site, os, glob, json

print('Python executable :', sys.executable)
print('Version           :', sys.version.split()[0])
print('Is virtualenv?    :', (hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)))
print()

print('site-packages locations:')
for p in site.getsitepackages():
    print('  ', p)
print()

try:
    user_site = site.getusersitepackages()
    print('user site-packages:', user_site)
except Exception:
    pass
print()

# Look for editable installs (the thing the agent was trying to find in the bug report)
print('Editable installs (__editable__*):')
found_editable = False
for base in site.getsitepackages():
    pattern = os.path.join(base, '__editable__*')
    for match in glob.glob(pattern):
        print('  ', match)
        found_editable = True
if not found_editable:
    print('  (none found in known site-packages)')
print()

print('Tip: To locate a specific package safely, run:')
print('  & \"' + sys.executable + '\" -c \"import eeagent_shared; print(eeagent_shared.__file__)\"  # replace with your package')
"@
    Write-Host "=============================================================================" -ForegroundColor Cyan
    Write-Host ""
}

# ============================================
# Convenient granular helpers (easy to call from the agent)
# ============================================

function Get-ActivePythonInfo {
    <#
    .SYNOPSIS
        Quick and safe way to answer "which Python am I actually running right now?"
        Use this instead of 'where python', 'python -c "where..."' or guessing.
    #>
    param(
        [string]$PythonExe = $null
    )

    if (-not $PythonExe) {
        $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $PythonExe) {
            Write-Warning "No 'python' found in current PATH."
            return
        }
    }

    if (-not (Test-Path $PythonExe)) {
        Write-Warning "Python not found at: $PythonExe"
        return
    }

    & $PythonExe -c @"
import sys, os
print('Active Python executable :', sys.executable)
print('Version                  :', sys.version.split()[0])
print('Is inside virtualenv?    :', (hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)))
print()
print('To get site-packages safely, run:')
print('  & \"' + sys.executable.replace('\\\\','/') + '\" -c \"import site; print(site.getsitepackages())\"')
"@
}

function Find-EditablePackages {
    <#
    .SYNOPSIS
        Safely finds all __editable__* packages (used by pip install -e).
        This is the safe replacement for the broken pattern:
            dir "...\site-packages\__editable__yourpkg*"
    #>
    param(
        [string]$PythonExe = $null
    )

    if (-not $PythonExe) {
        $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    }

    if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
        Write-Warning "Valid Python executable is required."
        return
    }

    Write-Host "Searching for editable installs (__editable__*)..." -ForegroundColor DarkGray

    & $PythonExe -c @"
import site, glob, os
found = False
for base in site.getsitepackages():
    pattern = os.path.join(base, '__editable__*')
    for match in glob.glob(pattern):
        print(match)
        found = True
if not found:
    print('(no editable installs found in known site-packages)')
"@
}

function Invoke-VenvPip {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $venvPython -m pip @Arguments 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    return $code
}

function Ensure-AgentixVenv {
# 1. Locate a RELIABLE base Python (never trust bare "python" on this machine)
Write-Host "`n[1/6] Locating reliable Python (rejecting Inkscape and other junk)..." -ForegroundColor Yellow

$script:basePython = Find-ReliablePython

if (-not $basePython) {
    $currentPy = $null
    try { $currentPy = (& python --version 2>&1) } catch {}
    $currentSrc = $null
    try { $currentSrc = (Get-Command python -ErrorAction SilentlyContinue).Source } catch {}

    Write-Error @"
No reliable Python 3.10+ found that is capable of creating a proper virtual environment.

Current "python" resolves to:
  Version: $currentPy
  Path   : $currentSrc

This machine has a polluted PATH (common when Inkscape or Git Bash is installed).

REQUIRED: The project MUST use exactly this environment:
  $ProjectRoot\.venv

Recommended fixes (try in order):

1. Install official Python 3.12 from https://www.python.org/downloads/
   (IMPORTANT: check "Add python.exe to PATH" + "Install Python Launcher (py.exe)")

2. If you already have Python installed somewhere, run the launcher explicitly:
   py -3.12 -m venv .venv

3. Add an exclusion for the whole project in Windows Defender (helps with .venv creation):
   Windows Security ? Virus & threat protection ? Manage settings ? Exclusions
   ? Add folder: $ProjectRoot

4. Run this script from an elevated (Administrator) PowerShell.

After installing a clean Python, re-run:
  powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1
"@
    exit 1
}

Write-Host "  Using reliable base Python: $basePython" -ForegroundColor Green

# 2. Robust venv handling � always create/repair the EXACT required .venv
Write-Host "`n[2/6] Ensuring virtual environment at $ProjectRoot\.venv ..." -ForegroundColor Yellow

$script:venvPath = Join-Path $ProjectRoot ".venv"
$script:activateScript = Join-Path $script:venvPath "Scripts\Activate.ps1"
$script:venvPython   = Join-Path $script:venvPath "Scripts\python.exe"

$needsRecreate = $false

if (Test-Path $venvPath) {
    if (-not (Test-Path $activateScript) -or -not (Test-Path $venvPython)) {
        Write-Host "  Existing .venv is broken (missing Activate.ps1 or python.exe). Recreating..." -ForegroundColor DarkYellow
        $needsRecreate = $true
    } else {
        try {
            $ver = & $venvPython --version 2>&1
            Write-Host "  Existing venv is valid ($ver)" -ForegroundColor Green
        } catch {
            Write-Host "  Existing venv Python is broken. Recreating..." -ForegroundColor DarkYellow
            $needsRecreate = $true
        }
    }
} else {
    $needsRecreate = $true
}

if ($needsRecreate) {
    if (Test-Path $venvPath) {
        Write-Host "  Removing old/broken .venv..." -ForegroundColor DarkYellow
        Remove-Item $venvPath -Recurse -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 400
    }

    Write-Host "  Creating new virtual environment using reliable Python..." -ForegroundColor Yellow
    & $basePython -m venv $venvPath
    $createExit = $LASTEXITCODE

    if ($createExit -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Error @"
Failed to create a working virtual environment at $venvPath

Base Python used: $basePython
Exit code: $createExit

Common causes on this machine:
- Windows Defender / antivirus is quarantining files inside .venv the moment they appear
- The base Python itself is partially broken or blocked

Immediate actions:
1. Add a permanent exclusion:
   Windows Security ? Virus & threat protection ? Manage settings ? Exclusions
   ? Add folder: $ProjectRoot\.venv   (and preferably the whole $ProjectRoot)

2. Try creating manually in an elevated shell:
   & "$basePython" -m venv .venv

3. Then re-run this script.

The agentic loop REQUIRES the environment at: $ProjectRoot\.venv
"@
        exit 1
    }

    # Wait for Activate.ps1 (antivirus delay protection)
    Write-Host "  Waiting for activation script..." -ForegroundColor Gray
    $maxWait = 18
    $waited  = 0
    $interval = 450

    while (-not (Test-Path $activateScript) -and $waited -lt $maxWait) {
        Start-Sleep -Milliseconds $interval
        $waited += ($interval / 1000.0)
    }

    if (-not (Test-Path $activateScript)) {
        Write-Error @"
Activate.ps1 did not appear after venv creation (waited $maxWait seconds).

This is a classic Windows Defender + Inkscape-PATH combination problem.

Please:
1. Add exclusion for $ProjectRoot\.venv
2. Delete the partial .venv folder manually
3. Re-run this script

Required environment location: $ProjectRoot\.venv
"@
        exit 1
    }

    Write-Host "  Virtual environment created successfully (took ~$([math]::Round($waited,1))s)." -ForegroundColor Green
}

# From this point we ALWAYS use the venv python directly
if (-not (Test-Path $venvPython)) {
    Write-Error "venv python.exe is missing after creation: $venvPython"
    exit 1
}

# 3. Activate (for interactive humans). Agents should prefer $venvPython directly.
Write-Host "`n[3/6] Activating virtual environment (for current shell)..." -ForegroundColor Yellow

if (-not (Test-Path $activateScript)) {
    Write-Error "Activation script is missing."
    exit 1
}

try {
    . $activateScript

    $currentPy = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($currentPy -and $currentPy -like "*\.venv\*") {
        Write-Host "  Virtual environment activated in this shell." -ForegroundColor Green
    } else {
        Write-Host "  Note: Activation did not override PATH in this session (common in agents)." -ForegroundColor DarkYellow
        Write-Host "  All further python calls in this script will use the full venv path." -ForegroundColor DarkYellow
    }
} catch {
    Write-Warning "Activation threw an error (non-fatal for agents): $_"
}

Write-Host "  ? From now on this script uses: $venvPython" -ForegroundColor DarkGray
}
