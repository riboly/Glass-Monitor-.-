# Restart Glass Monitor (ASCII only for reliability)
$ErrorActionPreference = "SilentlyContinue"
Set-Location -LiteralPath $PSScriptRoot
$script = Join-Path $PSScriptRoot "monitor.py"

# Stop old processes
Get-CimInstance Win32_Process |
  Where-Object {
    ($_.Name -eq "python.exe" -or $_.Name -eq "pythonw.exe") -and
    ($_.CommandLine -like "*$script*")
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Get-Process -Name typeperf, cpu_temp_helper -ErrorAction SilentlyContinue |
  Stop-Process -Force

Start-Sleep -Milliseconds 800

$candidates = @(
  "C:\Python314\pythonw.exe",
  "C:\Python314\python.exe",
  "C:\Python313\pythonw.exe",
  "C:\Python313\python.exe"
)

$py = $null
foreach ($c in $candidates) {
  if (Test-Path -LiteralPath $c) { $py = $c; break }
}
if (-not $py) {
  $cmd = Get-Command pythonw, python -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($cmd) { $py = $cmd.Source }
}

if (-not $py) {
  Write-Host "ERROR: Python not found"
  exit 1
}

Start-Process -FilePath $py -ArgumentList "`"$script`"" -WorkingDirectory $PSScriptRoot
Write-Host "OK started with $py"
exit 0
