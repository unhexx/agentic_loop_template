<#
.SYNOPSIS
    Robust initialization script for Blackbox + MiniMax2.5 agentic development in VSCode.

.DESCRIPTION
    Prepares a reliable local Python virtual environment and generates
    a high-quality starter prompt for the Agentic Loop.

    Key features:
    - Creates or repairs broken .venv even when PATH contains junk Pythons (Inkscape, etc.)
    - Auto-detects task from TODO.md or TASK_SPECIFICATION.md
    - Generates strong structured prompts with Pre-Flight Checklist + role temperatures
    - Supports generating reusable templates with {{PLACEHOLDERS}} via -GenerateTemplate
#>

[CmdletBinding()]
param(
    [string]$TaskDescription,
    [string]$TaskSpecFile = "TASK_SPECIFICATION.md",
    [string]$OutputFile,
    [switch]$GeneratePromptOnly,
    [int]$MaxTaskLength = 2800,
    [string]$ProjectName,
    [switch]$GenerateTemplate   # Generate reusable prompt with {{PLACEHOLDERS}} instead of filled content
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# Force UTF-8 everywhere possible (console + pipeline + file writing).
# This is critical on Russian Windows (default codepage = CP1251) to prevent mojibake
# in handoff JSON files and other text outputs.
# Wrapped in try/catch because some settings can fail in non-interactive sessions.
try {
    $OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    # Make all common file-writing cmdlets default to UTF-8 (without BOM where possible)
    $PSDefaultParameterValues['Out-File:Encoding']       = 'utf8'
    $PSDefaultParameterValues['Add-Content:Encoding']    = 'utf8'
    $PSDefaultParameterValues['Set-Content:Encoding']    = 'utf8'
    $PSDefaultParameterValues['Export-Csv:Encoding']     = 'utf8'
    $PSDefaultParameterValues['Get-Content:Encoding']    = 'utf8'   # So bare Get-Content / cat works nicely on UTF-8 files

    # Help Python-based tools the agent might call
    $env:PYTHONIOENCODING = "utf-8"
} catch {
    # Non-interactive / restricted environment � continue anyway
}

function Get-AutoTaskDescription {
    param([int]$MaxLength = 2800)

    $candidates = @(
        (Join-Path $ProjectRoot "TASK_SPECIFICATION.md"),
        (Join-Path $ProjectRoot "TODO.md")
    )

    foreach ($file in $candidates) {
        if (Test-Path $file) {
            try {
                # Use explicit UTF-8 via .NET to avoid Windows-1251 mojibake on Russian machines
                # (Get-Content without -Encoding uses the system ANSI codepage, which is deadly for UTF-8 files)
                $raw = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)
                if ($raw.Length -gt $MaxLength) {
                    $raw = $raw.Substring(0, $MaxLength) + "`n... (truncated)"
                }
                Write-Host "  Auto-detected task from: $(Split-Path $file -Leaf)" -ForegroundColor Green
                return $raw.Trim()
            } catch {}
        }
    }
    return $null
}

function Generate-AgentStarterPrompt {
    param(
        [string]$Task,
        [string]$SpecFile = "TASK_SPECIFICATION.md",
        [string]$ProjectName = "MyProject",
        [string]$ProjectRoot = ".",
        [switch]$AsTemplate
    )

    # Generates a strong, structured starter prompt for the Agentic Loop 3.1 self-learning.
    # When -AsTemplate is used, outputs a reusable version with clear {{PLACEHOLDERS}}.

    if ($AsTemplate) {
        # Reusable template version (ideal when copying agentic_loop_template to a new project)
        $prompt = @"
# Agentic Development Loop � Session Initialization (3.1 self-learning)

**Project:** {{ PROJECT_NAME }}

## Current Task / Specification

{{ TASK_SPECIFICATION }}

---

## MANDATORY FIRST ACTIONS (do these immediately)

1. **Environment Bootstrap**
   Run this command first:
   \`\`\`powershell
   powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1
   \`\`\`

2. **Activate Virtual Environment** (if not already active)
   \`\`\`powershell
   . .\.venv\Scripts\Activate.ps1
   \`\`\`

3. **Complete the Pre-Flight Checklist**
   Before doing any real work, open and fully complete the **Pre-Flight Checklist** section in:
   \`agentic_loop_template/SYSTEM_PROMPT.md\` (version 2.1)
   All placeholders must be verified or filled.

---

## REQUIRED READING ORDER (read in this exact sequence)

1. \`agentic_loop_template/README.md\`
2. \`agentic_loop_template/SYSTEM_PROMPT.md\` ? **Most important document**
3. \`agentic_loop_template/AGENT_ROLES.md\` (detailed instructions + recommended temperatures per role)
4. \`agentic_loop_template/HANDOFF_SCHEMA.md\` (strict JSON format for role transitions)
5. \`agentic_loop_template/Agent-Init.md\`
6. Current specification file (\`{{ SPEC_FILE }}\`)

Only after reading the above, proceed to act.

---

## Agentic Cycle Structure & Recommended Temperatures

You operate in a repeating closed loop:

| Role           | Temperature | Primary Responsibility                                      |
|----------------|-------------|-------------------------------------------------------------|
| **Orchestrator**  | 0.0         | Status assessment, planning, env prep, handoff to next role |
| **Coder**         | 0.2         | Implementation, code changes, migrations                    |
| **Tester**        | 0.0         | Writing/running tests, coverage, edge cases                 |
| **Debugger**      | 0.2         | Fixing bugs and test failures                               |
| **Reviewer**      | 0.0         | Spec compliance check, quality gate, decide next step       |

**Start this session as ORCHESTRATOR.**

After the Reviewer finishes, it decides whether to return control to the Orchestrator or conclude the task.

---

## Core Operating Rules (never break these)

- **Thinking discipline**: Always use PLAN ? ACT ? REFLECT. Never do more than 3 tool calls without an explicit REFLECT step.
- **Environment discipline**: All Python work happens exclusively inside the local \`.venv\`. Never use system \`python\` or \`pip\`.
- **Commit discipline**: Every git commit message must be written in natural Russian, in the voice of a real mid/senior developer who has been working on this project for months. **Strictly forbidden** to mention AI, LLM, agent, MiniMax, Grok, Claude, or any model name in commit messages.
- **Handoff discipline**: Role transitions must follow the exact JSON schema defined in \`HANDOFF_SCHEMA.md\`.
- **Self-improvement**: After each full cycle the Reviewer must update \`PROJECT_CONTEXT.md\` and \`SPRINTPLAN.md\`.
- **Quality bar**: All delivered code must be production-grade (typed, logged, error-handled, tested).

---

## Your Immediate Task as ORCHESTRATOR

After completing the reading and Pre-Flight Checklist:

1. Run the environment bootstrap script again (to ensure everything is fresh).
2. Perform a full project status assessment (\`git status\`, recent commits, key files).
3. Update or create \`PROJECT_CONTEXT.md\` with current state and cycle number.
4. Create or update \`SPRINTPLAN.md\` with clear, prioritized tasks for the current specification.
5. Begin the first planning phase according to the Orchestrator instructions in \`AGENT_ROLES.md\`.

Now begin.

---

**Template Version:** 2.1  |  Optimized for MiniMax 2.5 + Blackbox (non-interactive PowerShell)

---

## How to use this template for a new project

1. Copy the entire `agentic_loop_template/` folder into your new project.
2. Replace the placeholders above:
   - `{{ PROJECT_NAME }}` ? your project name
   - `{{ TASK_SPECIFICATION }}` ? content of your TODO.md or TASK_SPECIFICATION.md
   - `{{ SPEC_FILE }}` ? name of your spec file
3. (Optional) Customize the "MANDATORY FIRST ACTIONS" section for your environment.
4. Save as `starter_prompt.md` and use it as the first message to the agent.
"@
    }
    else {
        # Filled version for immediate use
        $venvActivate = ". .\.venv\Scripts\Activate.ps1"
        $agentInitCmd = "powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1"

        $prompt = @"
# Agentic Development Loop � Session Initialization (3.1 self-learning)

**Project:** $ProjectName

## Current Task / Specification

$Task

---

## MANDATORY FIRST ACTIONS (do these immediately)

1. **Environment Bootstrap**
   Run this command first:
   \`\`\`powershell
   $agentInitCmd
   \`\`\`

2. **Activate Virtual Environment** (if not already active)
   \`\`\`powershell
   $venvActivate
   \`\`\`

3. **Complete the Pre-Flight Checklist**
   Before doing any real work, open and fully complete the **Pre-Flight Checklist** section in:
   \`agentic_loop_template/SYSTEM_PROMPT.md\` (version 2.1)
   All placeholders must be verified or filled.

---

## REQUIRED READING ORDER (read in this exact sequence)

1. \`agentic_loop_template/README.md\`
2. \`agentic_loop_template/SYSTEM_PROMPT.md\` ? **Most important document**
3. \`agentic_loop_template/AGENT_ROLES.md\` (detailed instructions + recommended temperatures per role)
4. \`agentic_loop_template/HANDOFF_SCHEMA.md\` (strict JSON format for role transitions)
5. \`agentic_loop_template/Agent-Init.md\`
6. Current specification file ($SpecFile)

Only after reading the above, proceed to act.

---

## Agentic Cycle Structure & Recommended Temperatures

You operate in a repeating closed loop:

| Role           | Temperature | Primary Responsibility                                      |
|----------------|-------------|-------------------------------------------------------------|
| **Orchestrator**  | 0.0         | Status assessment, planning, env prep, handoff to next role |
| **Coder**         | 0.2         | Implementation, code changes, migrations                    |
| **Tester**        | 0.0         | Writing/running tests, coverage, edge cases                 |
| **Debugger**      | 0.2         | Fixing bugs and test failures                               |
| **Reviewer**      | 0.0         | Spec compliance check, quality gate, decide next step       |

**Start this session as ORCHESTRATOR.**

After the Reviewer finishes, it decides whether to return control to the Orchestrator or conclude the task.

---

## Core Operating Rules (never break these)

- **Thinking discipline**: Always use PLAN ? ACT ? REFLECT. Never do more than 3 tool calls without an explicit REFLECT step.
- **Environment discipline**: All Python work happens exclusively inside the local \`.venv\`. Never use system \`python\` or \`pip\`.
- **Commit discipline**: Every git commit message must be written in natural Russian, in the voice of a real mid/senior developer who has been working on this project for months. **Strictly forbidden** to mention AI, LLM, agent, MiniMax, Grok, Claude, or any model name in commit messages.
- **Handoff discipline**: Role transitions must follow the exact JSON schema defined in \`HANDOFF_SCHEMA.md\`.
- **Self-improvement**: After each full cycle the Reviewer must update \`PROJECT_CONTEXT.md\` and \`SPRINTPLAN.md\`.
- **Quality bar**: All delivered code must be production-grade (typed, logged, error-handled, tested).

---

## Your Immediate Task as ORCHESTRATOR

After completing the reading and Pre-Flight Checklist:

1. Run the environment bootstrap script again (to ensure everything is fresh).
2. Perform a full project status assessment (\`git status\`, recent commits, key files).
3. Update or create \`PROJECT_CONTEXT.md\` with current state and cycle number.
4. Create or update \`SPRINTPLAN.md\` with clear, prioritized tasks for the current specification.
5. Begin the first planning phase according to the Orchestrator instructions in \`AGENT_ROLES.md\`.

Now begin.

---

**Template Version:** 2.1  |  Optimized for MiniMax 2.5 + Blackbox (non-interactive PowerShell)
"@
    }

    return $prompt
}

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

# ============================================
# Main Logic - Robust Venv Handling
# ============================================

Write-Host "=== Agentic Loop Environment Initialization ===" -ForegroundColor Cyan
Write-Host "Reminder: Before starting work, the agent must complete the Pre-Flight Checklist in SYSTEM_PROMPT.md (version 2.1)." -ForegroundColor DarkGray
Write-Host "Project: $ProjectRoot" -ForegroundColor Gray

# 1. Locate a RELIABLE base Python (never trust bare "python" on this machine)
Write-Host "`n[1/6] Locating reliable Python (rejecting Inkscape and other junk)..." -ForegroundColor Yellow

$basePython = Find-ReliablePython

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

$venvPath = Join-Path $ProjectRoot ".venv"
$activateScript = Join-Path $venvPath "Scripts\Activate.ps1"
$venvPython   = Join-Path $venvPath "Scripts\python.exe"

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

# 4. Upgrade pip + install dependencies � ALWAYS via the venv python directly
# We must be extremely tolerant to stderr warnings from pip (NativeCommandError under $ErrorActionPreference=Stop)
Write-Host "`n[4/6] Upgrading pip and installing dependencies..." -ForegroundColor Yellow

function Invoke-VenvPip {
    param([Parameter(Mandatory=$true)][string[]]$Arguments)
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $venvPython -m pip @Arguments 2>&1 | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    return $code
}

$pipUpgradeExit = Invoke-VenvPip @('install', '--upgrade', 'pip', '--quiet')
if ($pipUpgradeExit -eq 0) {
    Write-Host "  pip upgraded." -ForegroundColor Green
} else {
    Write-Host "  Warning: pip upgrade returned exit code $pipUpgradeExit (continuing anyway)." -ForegroundColor DarkYellow
}

if (Test-Path (Join-Path $ProjectRoot "pyproject.toml")) {
    $instExit = Invoke-VenvPip @('install', '-e', "$ProjectRoot.[dev]", '--quiet')
    if ($instExit -eq 0) {
        Write-Host "  Dependencies installed from pyproject.toml." -ForegroundColor Green
    } else {
        Write-Host "  Warning: dependency install returned $instExit (may still work)." -ForegroundColor DarkYellow
    }
} elseif (Test-Path (Join-Path $ProjectRoot "requirements.txt")) {
    $instExit = Invoke-VenvPip @('install', '-r', (Join-Path $ProjectRoot "requirements.txt"), '--quiet')
    if ($instExit -eq 0) {
        Write-Host "  Dependencies installed from requirements.txt." -ForegroundColor Green
    } else {
        Write-Host "  Warning: dependency install returned $instExit." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "  No dependency file found. Skipping installation." -ForegroundColor DarkYellow
}

# 5. Set helpful environment variables
Write-Host "`n[5/6] Setting agent-friendly environment variables..." -ForegroundColor Yellow
[Environment]::SetEnvironmentVariable("POSH_BASH_CHAINING_NONINTERACTIVE", "1", "User")
$env:POSH_BASH_CHAINING_NONINTERACTIVE = "1"
Write-Host "  Variables set for non-interactive sessions." -ForegroundColor Green

# 6. Prompt generation
Write-Host "`n[6/6] Checking for task description..." -ForegroundColor Yellow

$finalTask = $TaskDescription
if (-not $finalTask) {
    $finalTask = Get-AutoTaskDescription -MaxLength $MaxTaskLength
}

# Auto-detect project name if not provided
if (-not $ProjectName) {
    $ProjectName = Split-Path $ProjectRoot -Leaf
}

if ($finalTask -or $GenerateTemplate) {
    $prompt = Generate-AgentStarterPrompt `
        -Task $finalTask `
        -SpecFile $TaskSpecFile `
        -ProjectName $ProjectName `
        -AsTemplate:$GenerateTemplate

    if ($OutputFile) {
        $out = if ([System.IO.Path]::IsPathRooted($OutputFile)) { $OutputFile } else { Join-Path $ProjectRoot $OutputFile }
        [System.IO.File]::WriteAllText($out, $prompt, [System.Text.Encoding]::UTF8)
        Write-Host "  Starter prompt saved to: $out" -ForegroundColor Green
    }

    if ($GenerateTemplate) {
        Write-Host "`n=== REUSABLE PROMPT TEMPLATE (with placeholders) ===" -ForegroundColor Cyan
        Write-Host "Use this version when copying the agentic_loop_template to a new project." -ForegroundColor DarkGray
    } else {
        Write-Host "`n=== Ready-to-use Starter Prompt for Blackbox (copy from here) ===" -ForegroundColor Yellow
        Write-Host "This prompt is optimized for MiniMax 2.5. It includes Pre-Flight Checklist, role temperatures, and strict instructions." -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host $prompt
    Write-Host ""
    Write-Host "=====================================================================" -ForegroundColor Yellow
} else {
    Write-Host "  No task description found automatically." -ForegroundColor DarkYellow
}

Write-Host ""
Write-Host "=== Agentic Loop Environment Ready ===" -ForegroundColor Green
Write-Host "REQUIRED venv location : $venvPath" -ForegroundColor White
Write-Host "Direct python executable: $venvPython" -ForegroundColor White
Write-Host "Use this in all agent steps: & `"$venvPython`" -m ..." -ForegroundColor DarkGray
Write-Host "=============================================" -ForegroundColor Green

# Automatic safe environment report (helps the agent avoid guessing paths)
# This directly prevents the class of errors where the agent does:
#   dir "%LOCALAPPDATA%... findstr leak"   or similar broken cmd.exe-style commands
Write-Host ""
Write-Host "Running automatic safe Python environment report..." -ForegroundColor DarkGray
Get-PythonEnvironmentReport -PythonExe $venvPython
Write-Host "If you later need to re-run this report safely, use:" -ForegroundColor DarkGray
Write-Host "  powershell -ExecutionPolicy Bypass -File .\agentic_loop_template\Agent-Init.ps1 -GeneratePromptOnly" -ForegroundColor DarkGray
Write-Host "  (then call the Get-PythonEnvironmentReport helper via the powershell tool)" -ForegroundColor DarkGray

Write-Host ""
Write-Host "Convenient one-liners for later use (when investigating packages):" -ForegroundColor DarkGray
# Write-Host "  Get active Python info:          powershell -Command \"&\" { . '.\agentic_loop_template\Agent-Init.ps1'; Get-ActivePythonInfo }\"" -ForegroundColor DarkGray
# Write-Host "  Find editable installs:          powershell -Command \"&\" { . '.\agentic_loop_template\Agent-Init.ps1'; Find-EditablePackages }\"" -ForegroundColor DarkGray
# Write-Host "  Full safe report (any python):   powershell -Command \"&\" { . '.\agentic_loop_template\Agent-Init.ps1'; Get-PythonEnvironmentReport -PythonExe '.\\.venv\\Scripts\\python.exe' }\"" -ForegroundColor DarkGray

Write-Host ""
Write-Host "Context Hygiene Reminder:" -ForegroundColor DarkGray
Write-Host "  After a full cycle (or when recent handoffs feel long), the Reviewer should perform Context Distillation." -ForegroundColor DarkGray
Write-Host "  See DEVELOPMENT_STANDARDS.md �8 and the Reviewer role instructions in AGENT_ROLES.md." -ForegroundColor DarkGray

Write-Host ""
Write-Host "Workspace Memory (Structured Institutional Memory):" -ForegroundColor DarkGray
Write-Host "  The project now has a Workspace-Scoped Structured Memory System (see DEVELOPMENT_STANDARDS.md �9)." -ForegroundColor DarkGray
Write-Host "  Orchestrator: always query snapshot early in the cycle." -ForegroundColor DarkGray
Write-Host "  Reviewer: extract patterns from lessons/distillation and update memory." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Quick commands (use after this script):" -ForegroundColor DarkGray
# Write-Host "    \"&\" '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' info" -ForegroundColor DarkGray
# Write-Host "    \"&\" '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' snapshot" -ForegroundColor DarkGray
# Write-Host "    \"&\" '.\agentic_loop_template\memory\Invoke-AgenticMemory.ps1' snapshot" -ForegroundColor DarkGray
# Write-Host "    \"&\" \".\\.venv\\Scripts\\python.exe\" -m agentic_loop_template.memory query --top 5" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Direct Python (when you have the venv path):" -ForegroundColor DarkGray
# Write-Host "    \"&\" \"$venvPython\" -m agentic_loop_template.memory snapshot" -ForegroundColor DarkGray
# Write-Host "    \"&\" \"$venvPython\" -m agentic_loop_template.memory query --top 3" -ForegroundColor DarkGray
