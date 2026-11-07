[CmdletBinding()]
param(
    [switch]$SetAsHome
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$sdk = if ($env:ANDROID_SDK_ROOT) {
    $env:ANDROID_SDK_ROOT
} elseif ($env:ANDROID_HOME) {
    $env:ANDROID_HOME
} else {
    Join-Path $env:LOCALAPPDATA 'Android\Sdk'
}

$adb = Join-Path $sdk 'platform-tools\adb.exe'
$buildToolsRoot = Join-Path $sdk 'build-tools'
$buildToolDir = Get-ChildItem -LiteralPath $buildToolsRoot -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'apksigner.bat') } |
    Sort-Object { [version]$_.Name } -Descending |
    Select-Object -First 1

$javaHome = if ($env:JAVA_HOME) {
    $env:JAVA_HOME
} elseif (Test-Path 'C:\Program Files\Android\Android Studio\jbr') {
    'C:\Program Files\Android\Android Studio\jbr'
} else {
    $null
}

foreach ($path in @($adb, $buildToolDir.FullName, $javaHome)) {
    if (!$path -or !(Test-Path -LiteralPath $path)) {
        throw "Required Android tool was not found: $path"
    }
}

$apksigner = Join-Path $buildToolDir.FullName 'apksigner.bat'
$out = Join-Path $root 'tv-app\out'
$keystore = Join-Path $root 'tv-app\debug.keystore'
$alignedApk = Join-Path $out 'urlsteam-tv-aligned.apk'
$signedApk = Join-Path $out 'urlsteam-tv.apk'

$env:ANDROID_USER_HOME = Join-Path $root 'tv-app\out\.android'
New-Item -ItemType Directory -Force -Path $env:ANDROID_USER_HOME | Out-Null

& (Join-Path $root 'build.ps1')
if (!(Test-Path -LiteralPath $alignedApk)) {
    throw "Unsigned aligned APK was not created: $alignedApk"
}

if (!(Test-Path -LiteralPath $keystore)) {
    & (Join-Path $javaHome 'bin\keytool.exe') -genkeypair `
        -keystore $keystore -storepass android -alias androiddebugkey `
        -keypass android -keyalg RSA -keysize 2048 -validity 10000 `
        -dname 'CN=Android Debug,O=Android,C=US'
    if ($LASTEXITCODE -ne 0) { throw 'Debug keystore creation failed' }
}

& $apksigner sign --ks $keystore --ks-pass pass:android `
    --ks-key-alias androiddebugkey --key-pass pass:android `
    --out $signedApk $alignedApk
if ($LASTEXITCODE -ne 0) { throw 'APK signing failed' }

& $adb install -r $signedApk
if ($LASTEXITCODE -ne 0) { throw 'APK installation failed' }

if ($SetAsHome) {
    & $adb shell cmd package set-home-activity com.urlsteam.tv/.MainActivity
    if ($LASTEXITCODE -ne 0) { throw 'Could not set urlSteam TV as the home activity' }
}

Write-Output "Installed urlSteam TV: $signedApk"
