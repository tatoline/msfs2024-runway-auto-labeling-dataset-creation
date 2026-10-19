param(
    [string]$SdkRoot = $env:MSFS2024_SDK,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'

if (-not $SdkRoot) {
    $candidates = @(
        'C:\MSFS 2024 SDK',
        'C:\MSFS2024 SDK',
        "$env:USERPROFILE\Documents\MSFS 2024 SDK"
    )
    $SdkRoot = $candidates | Where-Object {
        Test-Path -LiteralPath (Join-Path $_ 'SimConnect SDK\include\SimConnect.h')
    } | Select-Object -First 1
}
$header = if ($SdkRoot) { Join-Path $SdkRoot 'SimConnect SDK\include\SimConnect.h' } else { '' }
if (-not $SdkRoot -or -not (Test-Path -LiteralPath $header)) {
    throw 'MSFS 2024 SDK not found. In MSFS Developer Mode use Help > SDK Installer, then run: .\build.ps1 -SdkRoot "C:\path\to\MSFS 2024 SDK"'
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Visual Studio Installer (vswhere.exe) was not found.' }
$msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
if (-not $msbuild) { throw 'MSBuild was not found. Install Visual Studio Desktop development with C++.' }

Write-Host "SDK:     $SdkRoot"
Write-Host "MSBuild: $msbuild"
if ($Check) { exit 0 }

& $msbuild (Join-Path $PSScriptRoot 'camera_bridge.vcxproj') /m /p:Configuration=Release /p:Platform=x64 "/p:MSFS2024_SDK=$SdkRoot"
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }

$runtimeCandidates = @(
    (Join-Path $SdkRoot 'SimConnect SDK\lib\SimConnect.dll'),
    (Join-Path $SdkRoot 'SimConnect SDK\lib\static\SimConnect.dll')
)
$runtime = $runtimeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($runtime) {
    Copy-Item -LiteralPath $runtime -Destination (Join-Path $PSScriptRoot 'bin\Release\SimConnect.dll') -Force
}
Write-Host "Built: $(Join-Path $PSScriptRoot 'bin\Release\camera_bridge.exe')"
