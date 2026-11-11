# Build the CIDINT OS ISO from Windows through WSL2.
#   powershell -ExecutionPolicy Bypass -File build.ps1
# The ISO is written to .\out\cidint-os-amd64.iso
param([string]$Distro = "")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-Wsl {
    try {
        $out = (& wsl.exe -l -q 2>$null) -replace "`0", "" | Where-Object { $_.Trim() }
        return ,@($out)
    } catch { return ,@() }
}

$distros = Test-Wsl
if ($LASTEXITCODE -ne 0 -or $distros.Count -eq 0) {
    Write-Host ""
    Write-Host "WSL2 with a Linux distribution is required to build the ISO, and it is not working on this PC." -ForegroundColor Yellow
    Write-Host "Fix it once, from an *Administrator* PowerShell:"
    Write-Host ""
    Write-Host "    wsl --install -d Debian"
    Write-Host ""
    Write-Host "Reboot, let Debian finish its first-run setup, then run this script again."
    Write-Host "Alternative without WSL: push this folder to GitHub; .github/workflows/build-iso.yml builds the ISO in the cloud."
    exit 1
}
if (-not $Distro) {
    $Distro = ($distros | Where-Object { $_ -match "Debian|Ubuntu" } | Select-Object -First 1)
    if (-not $Distro) { $Distro = $distros[0] }
}
$Distro = $Distro.Trim()

# C:\Users\me\Cidint OS  ->  /mnt/c/Users/me/Cidint OS
$drive = $root.Substring(0, 1).ToLower()
$mnt = "/mnt/$drive" + ($root.Substring(2) -replace "\\", "/")

Write-Host "Building CIDINT OS in WSL distribution '$Distro' (30-90 minutes, needs ~25 GB free)..." -ForegroundColor Cyan
$cmd = "sed 's/\r`$//' '$mnt/build.sh' > /tmp/cidint-build.sh && CIDINT_SRC='$mnt' CIDINT_OUT='$mnt/out' bash /tmp/cidint-build.sh"
& wsl.exe -d $Distro -u root -- bash -c $cmd
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed. See the output above and out\build-amd64.log" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "ISO ready in $root\out" -ForegroundColor Green
