$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'scripts/launch.py'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $launcher @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $launcher @args
} else {
    Write-Error 'Install Python 3.10+ and Node.js 22.18+, then run this script again.'
    exit 1
}
exit $LASTEXITCODE
