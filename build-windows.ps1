$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if ($env:OS -ne 'Windows_NT') { throw 'Run this script on Windows 10/11 x64.' }
function Invoke-Checked {
    param([string]$Exe, [string[]]$ArgsList)
    & $Exe @ArgsList
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Exe $ArgsList" }
}
Invoke-Checked -Exe 'py' -ArgsList @('-3.12','-m','venv','.venv-build')
$Python = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
Invoke-Checked -Exe $Python -ArgsList @('-m','pip','install','-r','requirements-dev.txt')
$env:QT_QPA_PLATFORM = 'offscreen'
Invoke-Checked -Exe $Python -ArgsList @('-m','pytest','-q','--junitxml=build-test-results.xml')
Invoke-Checked -Exe $Python -ArgsList @('-m','PyInstaller','--clean','--noconfirm','STCatalogManager.spec')
$SmokeDir = Join-Path $PSScriptRoot 'build\smoke-data'
$Executable = Join-Path $PSScriptRoot 'dist\STCatalogManager\STCatalogManager.exe'
$Process = Start-Process -FilePath $Executable -ArgumentList @('--smoke-test','--data-dir',('"' + $SmokeDir + '"')) -PassThru
if (-not $Process.WaitForExit(60000)) { $Process.Kill(); throw 'Packaged GUI smoke test timed out.' }
if ($Process.ExitCode -ne 0) { throw 'Packaged GUI smoke test failed.' }
if (-not (Test-Path (Join-Path $SmokeDir 'catalog.sqlite3'))) { throw 'Seeded database was not created.' }
Remove-Item Env:QT_QPA_PLATFORM
Copy-Item 'README.md' 'dist\STCatalogManager\README.md'
Copy-Item 'docs' 'dist\STCatalogManager\docs' -Recurse -Force
Compress-Archive -Path 'dist\STCatalogManager' -DestinationPath 'dist\STCatalogManager-Windows-x64.zip' -Force
Write-Host 'Ready: dist\STCatalogManager-Windows-x64.zip'
Write-Host 'Run STCatalogManager.exe inside the extracted folder. Keep the _internal folder next to it.'
