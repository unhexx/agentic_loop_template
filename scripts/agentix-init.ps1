# Install Agentix into a new project and configure it in one command.
#   powershell -ExecutionPolicy Bypass -File scripts\agentix-init.ps1 C:\work\my-app --name my-app --frontend grok
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$Root;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $Root
}
& python -m memory init --template $Root @args
exit $LASTEXITCODE
