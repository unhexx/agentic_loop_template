# setup_env.ps1 — Environment preparation for the project (generic template)
# Run: powershell -ExecutionPolicy Bypass -File .\scripts\setup_env.ps1

param(
    [string]$PythonVersion = "3.11",
    [string]$VenvDir = ".venv",
    [string]$RequirementsFile = "requirements.txt"
)

$ErrorActionPreference = "Stop"

Write-Host "=== Preparing environment ===" -ForegroundColor Cyan

# ─── 1. Check Python ────────────────────────────────────────────────────────
Write-Host "`n[1/6] Checking Python..." -ForegroundColor Yellow
$pythonExe = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $version = & $candidate --version 2>&1
        if ($version -match "Python $PythonVersion") {
            $pythonExe = $candidate
            Write-Host "  ✓ Найден: $version ($candidate)" -ForegroundColor Green
            break
        }
    } catch { }
}

if (-not $pythonExe) {
    Write-Error "Python $PythonVersion not found. Install Python $PythonVersion and add it to PATH."
    exit 1
}

# ─── 2. Create virtual environment ───────────────────────────────────────
Write-Host "`n[2/6] Creating virtual environment in $VenvDir..." -ForegroundColor Yellow
if (Test-Path $VenvDir) {
    Write-Host "  ✓ Окружение уже существует, пропускаем создание." -ForegroundColor Green
} else {
    & $pythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Write-Error "Не удалось создать venv."; exit 1 }
    Write-Host "  ✓ Окружение создано." -ForegroundColor Green
}

# ─── 3. Активация окружения ───────────────────────────────────────────────────
Write-Host "`n[3/6] Активация окружения..." -ForegroundColor Yellow
$activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $activateScript)) {
    Write-Error "Скрипт активации не найден: $activateScript"
    exit 1
}
. $activateScript
Write-Host "  ✓ Окружение активировано." -ForegroundColor Green

# ─── 4. Обновление pip (через venv python) ─────────────────────────────────────
Write-Host "`n[4/6] Обновление pip..." -ForegroundColor Yellow
& $VenvPython -m pip install --upgrade pip --quiet 2>&1 | Out-Null
Write-Host "  ✓ pip обновлён." -ForegroundColor Green

# ─── 5. Установка зависимостей ────────────────────────────────────────────────
Write-Host "`n[5/6] Установка зависимостей из $RequirementsFile..." -ForegroundColor Yellow
if (Test-Path $RequirementsFile) {
    & $VenvPython -m pip install -r $RequirementsFile --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "Ошибка установки зависимостей."; exit 1 }
    Write-Host "  ✓ Зависимости установлены." -ForegroundColor Green
} elseif (Test-Path "pyproject.toml") {
    & $VenvPython -m pip install -e ".[dev]" --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "Ошибка установки через pyproject.toml."; exit 1 }
    Write-Host "  ✓ Установлено через pyproject.toml." -ForegroundColor Green
} else {
    Write-Host "  ⚠ Файл зависимостей не найден ($RequirementsFile / pyproject.toml). Пропускаем." -ForegroundColor DarkYellow
}

# ─── 6. Проверка git ──────────────────────────────────────────────────────────
Write-Host "`n[6/6] Проверка git..." -ForegroundColor Yellow
try {
    $gitVersion = git --version 2>&1
    Write-Host "  ✓ $gitVersion" -ForegroundColor Green
    
    $currentBranch = git rev-parse --abbrev-ref HEAD 2>&1
    Write-Host "  ✓ Текущая ветка: $currentBranch" -ForegroundColor Green
    
    $gitStatus = git status --short 2>&1
    if ($gitStatus) {
        Write-Host "  ⚠ Незакоммиченные изменения:" -ForegroundColor DarkYellow
        $gitStatus | ForEach-Object { Write-Host "    $_" }
    } else {
        Write-Host "  ✓ Рабочая директория чистая." -ForegroundColor Green
    }
} catch {
    Write-Host "  ⚠ git не найден или не инициализирован." -ForegroundColor DarkYellow
}

# ─── Итог ─────────────────────────────────────────────────────────────────────
Write-Host "`n=== Окружение готово к работе ===" -ForegroundColor Cyan
Write-Host "Для активации окружения в новой сессии: . .\$VenvDir\Scripts\Activate.ps1" -ForegroundColor Gray

# ─── 7. Обработка .env для секретов (учёт требований ИБ и петли саморазвития) ────────
Write-Host "`n[7/7] Настройка .env для токенов/секретов и переменных окружения..." -ForegroundColor Yellow
$envFile = ".env"
$envExample = ".env.example"
$giFile = ".gitignore"

if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile -Force
        Write-Host "  ✓ .env создан из .env.example (заполните реальные значения вручную перед запуском)" -ForegroundColor Green
    } else {
        $envContent = @"
# your_project environment - STORE SECRETS HERE
# This file MUST NOT end up in git (see .gitignore)
# Follow best security practices: do not store plaintext secrets in code, use encryption where possible,
# for production - integrate with vault/keyring/OS credential manager.
# After moving to an isolated environment, secret/SSH credential handling is performed exclusively via local processing inside the sandbox.

YOURPROJECT_ENV=development
# Example SSH for deployment (provided to the user for the demo stand)
# SSH_HOST=your-demo-host
# SSH_USER=demo-user
# SSH_KEY_PATH=~/.ssh/demo_key   # or use keyring instead of file

# Other secrets (MCP tokens, API keys etc.)
# CENTRAL_TOKEN=...
"@
        $envContent | Out-File -FilePath $envFile -Encoding utf8 -Force
        Write-Host "  ✓ .env создан с шаблоном (обновите значения!)" -ForegroundColor Green
    }
} else {
    Write-Host "  ✓ .env уже существует." -ForegroundColor Green
}

# Убедиться, что .env в .gitignore
if (Test-Path $giFile) {
    $giContent = Get-Content $giFile -Raw
    if ($giContent -notmatch '(?m)^\s*\.env\s*$') {
        Add-Content $giFile -Value "`n# Secrets and env vars - high IB requirement`n.env`n.env.local`n*.pem`n*.key"
        Write-Host "  ✓ .env добавлен в .gitignore" -ForegroundColor Green
    }
}

# Создать .env.example если нет (для шаблона)
if (-not (Test-Path $envExample)) {
    $exampleContent = @"
# your_project environment example
# Copy to .env and fill in

YOURPROJECT_ENV=development
# SSH for demo (provide to the user)
# SSH_HOST=
# SSH_USER=
# SSH_PORT=22
"@
    $exampleContent | Out-File -FilePath $envExample -Encoding utf8
    Write-Host "  ✓ .env.example создан для будущих копий" -ForegroundColor Green
}
