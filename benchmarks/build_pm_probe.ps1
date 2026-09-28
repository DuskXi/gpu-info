param([string]$CudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6')
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$installation = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $installation) { throw 'MSVC x64 tools are required.' }
Import-Module (Join-Path $installation 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $installation -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64'
$outputDirectory = Join-Path $PSScriptRoot 'results'
New-Item -ItemType Directory -Force $outputDirectory | Out-Null
$cupti = Join-Path $CudaRoot 'extras\CUPTI'
& cl /nologo /utf-8 /EHsc /std:c++17 /O2 /I "$CudaRoot\include" /I "$cupti\include" `
    "$PSScriptRoot\cupti_pm_probe.cpp" "/Fo:$outputDirectory\cupti_pm_probe.obj" "/Fe:$outputDirectory\cupti_pm_probe.exe" `
    /link "/LIBPATH:$CudaRoot\lib\x64" "/LIBPATH:$cupti\lib64" cuda.lib cupti.lib
if ($LASTEXITCODE -ne 0) { throw 'Probe compilation failed.' }
Write-Output "Built $outputDirectory\cupti_pm_probe.exe; add $cupti\lib64 to PATH before running."
