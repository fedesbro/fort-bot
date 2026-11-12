# Compila titan_helper.exe con l'SDK di TitanCSA/Interceptor-Keyboard-Mouse.
# Richiede: git e "Visual Studio 2022 Build Tools" con il carico di lavoro C++ (Desktop development with C++).
# Uso (PowerShell):  .\titan\build.ps1
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$sdk  = Join-Path $here "sdk"

if (-not (Test-Path (Join-Path $sdk "Interceptor\Interceptor.cpp"))) {
    git clone --depth 1 https://github.com/TitanCSA/Interceptor-Keyboard-Mouse $sdk
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "Visual Studio Build Tools non trovati: installali con il carico 'Sviluppo di applicazioni desktop con C++'." }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw "Manca il componente C++ (MSVC x64) di Visual Studio." }
$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"

$inc  = Join-Path $sdk "Interceptor"
$cl = "cl /nologo /std:c++17 /EHsc /O2 /DUNICODE /D_UNICODE /DNOMINMAX /I`"$inc`" " +
      "`"$here\helper.cpp`" `"$inc\Interceptor.cpp`" /Fe:`"$here\titan_helper.exe`" /Fo:`"$here\\`" " +
      "/link setupapi.lib newdev.lib cfgmgr32.lib hid.lib advapi32.lib user32.lib comctl32.lib ole32.lib"
cmd /c "call `"$vcvars`" >nul && $cl"
if ($LASTEXITCODE -ne 0) { throw "Compilazione fallita (codice $LASTEXITCODE)." }
Write-Host "OK: $here\titan_helper.exe"
