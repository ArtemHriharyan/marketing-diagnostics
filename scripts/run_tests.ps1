param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$tempRoot = Join-Path $repoRoot "tmp"
$pytestTemp = Join-Path $repoRoot ".pytest_tmp"
$venvPython = Join-Path $repoRoot ".venv\\Scripts\\python.exe"

try {
    New-Item -ItemType Directory -Force -Path $tempRoot, $pytestTemp -ErrorAction Stop | Out-Null
}
catch {
    Write-Error "Не удалось создать локальные временные каталоги: $($_.Exception.Message)"
    exit 2
}

$env:TMP = $tempRoot
$env:TEMP = $tempRoot
if (Test-Path $venvPython) {
    & $venvPython -m pytest -p no:cacheprovider --basetemp=$pytestTemp @PytestArgs
}
else {
    & python -m pytest -p no:cacheprovider --basetemp=$pytestTemp @PytestArgs
}
exit $LASTEXITCODE
