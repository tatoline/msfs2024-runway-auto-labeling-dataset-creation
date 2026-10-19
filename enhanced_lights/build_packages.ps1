param(
    [Parameter(Mandatory=$true)][string]$ProjectRoot,
    [Parameter(Mandatory=$true)][string]$SdkRoot,
    [int]$TimeoutSeconds = 600
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name 'FlightSimulator2024' -ErrorAction SilentlyContinue) {
    throw 'Fully close MSFS before building scenery packages.'
}
$tool = Join-Path $SdkRoot 'Tools\bin\fspackagetool.exe'
if (-not (Test-Path -LiteralPath $tool)) { throw 'fspackagetool.exe was not found in SdkRoot.' }
$resolvedProjects = (Resolve-Path -LiteralPath $ProjectRoot).Path
$projects = Get-ChildItem -LiteralPath $resolvedProjects -Filter '*_Enhanced_Lights.xml' -File -Recurse
if (-not $projects) { throw 'No prepared projects found. Run prepare_packages.py first.' }
foreach ($project in $projects) {
    $buildRoot = $project.DirectoryName
    $log = Join-Path $buildRoot 'build.log'
    $errorLog = Join-Path $buildRoot 'build-errors.log'
    $arguments = '"{0}" -outputdir "{1}" -tempdir "{1}" -rebuild -nopause' -f $project.FullName, $buildRoot
    Write-Host "Building $($project.Name)"
    $process = Start-Process -FilePath $tool -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError $errorLog
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while (-not $process.HasExited) {
        Start-Sleep -Seconds 1
        $process.Refresh()
        $tooLarge = Get-ChildItem -LiteralPath $buildRoot -File -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in '.bgl','.tmp' -and $_.Length -gt 100MB }
        if ((Get-Date) -gt $deadline -or $tooLarge) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Build timed out or exceeded output-size guard. Inspect $log and any SDK-started compiler process before retrying."
        }
    }
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "Build failed. Read $log and $errorLog" }
    $packages = Join-Path $buildRoot 'Packages'
    $layouts = Get-ChildItem -LiteralPath $packages -Filter 'layout.json' -File -Recurse -ErrorAction SilentlyContinue
    $bgls = Get-ChildItem -LiteralPath $packages -Filter '*.bgl' -File -Recurse -ErrorAction SilentlyContinue
    if (-not $layouts -or -not $bgls) { throw "Build reported completion without a layout/BGL. Inspect $log" }
    Write-Host "Built package folder: $packages"
}
Write-Host 'Build complete. Copy each generated package folder into your MSFS 2024 Community folder, then restart MSFS.'
