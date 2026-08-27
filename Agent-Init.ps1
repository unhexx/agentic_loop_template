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
    [switch]$GenerateTemplate,   # Generate reusable prompt with {{PLACEHOLDERS}} instead of filled content
    [switch]$Wizard,
    [string]$Frontend
)

$ErrorActionPreference = "Stop"
# pip/схема — корень шаблона; .venv и TASK_SPEC — корень продукта (README vs Agent-Init.md)
if (Test-Path (Join-Path $PSScriptRoot "memory\supervisor.py")) {
    $TemplateRoot = $PSScriptRoot
} else {
    $TemplateRoot = Split-Path -Parent $PSScriptRoot
}
$ProjectRoot = $TemplateRoot
# вложенный вызов: cd product; .\agentic_loop_template\Agent-Init.ps1
if ((Split-Path -Leaf $PSScriptRoot) -eq 'agentic_loop_template') {
    $parentOfScript = Split-Path -Parent $PSScriptRoot
    try {
        $cwdFull = [System.IO.Path]::GetFullPath((Get-Location).Path).TrimEnd('\', '/').ToLowerInvariant()
        $parentFull = [System.IO.Path]::GetFullPath($parentOfScript).TrimEnd('\', '/').ToLowerInvariant()
        if ($cwdFull -eq $parentFull) {
            $ProjectRoot = $parentOfScript
        }
    } catch {}
}

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

# Хелперы после UTF-8: иначе here-string/CP1251 ломают кириллицу в PS 5.1
. (Join-Path $PSScriptRoot "scripts\windows\Init-Python.ps1")
. (Join-Path $PSScriptRoot "scripts\windows\Init-Prompt.ps1")

Write-Host "=== Agentic Loop Environment Initialization ===" -ForegroundColor Cyan
Write-Host "Reminder: Before starting work, the agent must complete the Pre-Flight Checklist in SYSTEM_PROMPT.md (version 2.1)." -ForegroundColor DarkGray
Write-Host "Project: $ProjectRoot" -ForegroundColor Gray
if ($ProjectRoot -ne $TemplateRoot) {
    Write-Host "Template: $TemplateRoot" -ForegroundColor Gray
}

Ensure-AgentixVenv

$pipUpgradeExit = Invoke-VenvPip @('install', '--upgrade', 'pip', '--quiet')
if ($pipUpgradeExit -eq 0) {
    Write-Host "  pip upgraded." -ForegroundColor Green
} else {
    Write-Host "  Warning: pip upgrade returned exit code $pipUpgradeExit (continuing anyway)." -ForegroundColor DarkYellow
}

if (Test-Path (Join-Path $TemplateRoot "pyproject.toml")) {
    # extras: путь сразу плюс [dev], без точки перед скобками
    $instExit = Invoke-VenvPip @('install','-e',"$TemplateRoot[dev]")
    if ($instExit -ne 0) {
        $instExit = Invoke-VenvPip @('install', 'jsonschema>=4.18,<5', 'pytest>=8.0,<9')
    }
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

# PYTHONPATH — запасной путь, если editable-install не подхватился
if (-not $env:PYTHONPATH) { $env:PYTHONPATH = $TemplateRoot } elseif ($env:PYTHONPATH -notlike "*$TemplateRoot*") { $env:PYTHONPATH = "$TemplateRoot;$env:PYTHONPATH" }

# Frontend: -Frontend > визард (grok) > supervisor.adapter > blackbox
$explicitFrontend = -not [string]::IsNullOrWhiteSpace($Frontend)
if ($explicitFrontend) {
    $initFe = $Frontend.Trim().ToLowerInvariant()
} else {
    $initFe = "blackbox"
    $cfgPath = Join-Path $ProjectRoot ".agent\project_config.json"
    if (-not (Test-Path $cfgPath)) { $cfgPath = Join-Path $ProjectRoot ".agent\project_config.example.json" }
    if (Test-Path $cfgPath) {
        try {
            $cfgObj = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfgObj.supervisor -and $cfgObj.supervisor.adapter) {
                $initFe = [string]$cfgObj.supervisor.adapter
            }
        } catch {}
    }
}

if ($Wizard) {
    Write-Host ""
    Write-Host "=== Agentix Onboarding Wizard ===" -ForegroundColor Cyan
    $nameIn = ""
    $platChoice = ""
    $feChoice = ""
    $specIn = ""
    try { $nameIn = Read-Host "Project name" } catch {}
    if (-not $ProjectName) {
        if ($nameIn) { $ProjectName = $nameIn } else { $ProjectName = "my-project" }
    }
    Write-Host "Platform: 1) Linux 2) macOS 3) Windows (via WSL)"
    try { $platChoice = Read-Host "Choice [1]" } catch {}
    Write-Host "Frontend: 1) Grok  2) Cursor  3) Claude Code  4) Blackbox"
    try { $feChoice = Read-Host "Choice [1]" } catch {}
    try { $specIn = Read-Host "Spec file [TASK_SPECIFICATION.md]" } catch {}
    if ($specIn) { $TaskSpecFile = $specIn }

    if (-not $explicitFrontend) {
        switch ($feChoice) {
            "2" { $initFe = "cursor" }
            "3" { $initFe = "claude" }
            "4" { $initFe = "blackbox" }
            default { $initFe = "grok" }
        }
    }

    $specDest = if ([System.IO.Path]::IsPathRooted($TaskSpecFile)) { $TaskSpecFile } else { Join-Path $ProjectRoot $TaskSpecFile }
    $exampleSpec = Join-Path $TemplateRoot "examples\consumer-starter\TASK_SPECIFICATION.example.md"
    if (-not (Test-Path $specDest) -and (Test-Path $exampleSpec)) {
        Copy-Item -LiteralPath $exampleSpec -Destination $specDest -Force
        Write-Host "  Created $specDest from consumer-starter template" -ForegroundColor DarkGray
    }
    $ctxDest = Join-Path $ProjectRoot "PROJECT_CONTEXT.md"
    $exampleCtx = Join-Path $TemplateRoot "examples\consumer-starter\PROJECT_CONTEXT.example.md"
    if (-not (Test-Path $ctxDest) -and (Test-Path $exampleCtx)) {
        Copy-Item -LiteralPath $exampleCtx -Destination $ctxDest -Force
        Write-Host "  Created PROJECT_CONTEXT.md from template" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host "Setup complete for: $ProjectName"
    Write-Host "  Platform choice: $(if ($platChoice) { $platChoice } else { '1' })"
    Write-Host "  Frontend: $initFe"
    Write-Host "  Spec: $TaskSpecFile"
}

try {
    $oldPref = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $venvPython -m memory.proxy install-venv 2>$null | Out-Null
    & $venvPython -m memory state init 2>$null | Out-Null
    & $venvPython -m memory.experience_harvester seed-defaults --apply 2>$null | Out-Null
    & $venvPython -m memory.knowledge ingest-if-empty --root docs --budget 800 2>$null | Out-Null
    & $venvPython -m memory.playbooks seed --from-standards 2>$null | Out-Null
    & $venvPython -m memory.context_budget cold-start --budget 16000 --compress 2>$null | Out-Null
    $ErrorActionPreference = $oldPref
} catch {
    Write-Host "  Proxy install-venv skipped." -ForegroundColor DarkYellow
}

# визард / явный grok|cursor|blackbox — fail-closed; иначе best-effort (CI без pxpipe)
# mock никогда не валит bootstrap; AGENTIX_PROXY=0 по-прежнему opt-out внутри health --init
$healthOut = & $venvPython -m memory.proxy health --init --frontend $initFe --workdir $ProjectRoot 2>&1
$healthRc = $LASTEXITCODE
$liveExplicit = @('grok', 'cursor', 'blackbox')
$failClosed = ($initFe -ne 'mock') -and (
    $Wizard -or ($explicitFrontend -and ($liveExplicit -contains $initFe))
)
if ($failClosed -and $healthRc -ne 0) {
    Write-Host "AGENT_INIT: pxpipe required for frontend=$initFe (or export AGENTIX_PROXY=0)" -ForegroundColor Red
    Write-Host $healthOut
    exit 1
}
Write-Host "  Proxy env exports installed (frontend=$initFe)." -ForegroundColor DarkGray

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

# короткий файл холодного старта — как Init.sh, даже без автодетекта задачи
$agentDir = Join-Path $ProjectRoot ".agent"
if (-not (Test-Path $agentDir)) {
    New-Item -ItemType Directory -Path $agentDir -Force | Out-Null
}
$shortPromptPath = Join-Path $agentDir "starter_prompt_grok.txt"
$verFile = Join-Path $TemplateRoot "VERSION"
$tplVersion = "3.9.0"
if (Test-Path $verFile) {
    $tplVersion = ([System.IO.File]::ReadAllText($verFile, [System.Text.Encoding]::UTF8)).Trim()
}
$shortPrompt = @"
You are running the Agentic Development Loop (template $tplVersion).

Cold-start (first, max 4 tool calls):
1. python -m memory.proxy health
2. python -m memory state snapshot --window 3
3. python -m memory query --top 5 --category "Common Failure Patterns"
4. python -m memory.knowledge query --q "cycle" --top 3

Then act as Orchestrator:
- prompts/short_orchestrator_prompt.md; .agent/PLAN.md + TODO if present
- Playbooks: python -m memory.playbooks select when available
- Do NOT load multi-MB .agent/history archives
- PLAN -> ACT (<=3 tools) -> REFLECT; one JSON handoff; validate with memory.validate_handoff
- Commits: natural Russian human voice, no AI/model mentions
- Parallel: PARALLEL_PROTOCOL.md + scripts/agentic_loop.sh

Begin as Orchestrator.
"@
[System.IO.File]::WriteAllText($shortPromptPath, $shortPrompt, [System.Text.Encoding]::UTF8)
Write-Host "  Starter prompt saved to: $shortPromptPath" -ForegroundColor Green

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
