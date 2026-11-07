[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Join-Path $root 'tv-app'
$app = Join-Path $project 'app'
$out = Join-Path $project 'out'
$classes = Join-Path $out 'classes'
$dex = Join-Path $out 'dex'
$generated = Join-Path $out 'gen'

$sdk = if ($env:ANDROID_SDK_ROOT) {
    $env:ANDROID_SDK_ROOT
} elseif ($env:ANDROID_HOME) {
    $env:ANDROID_HOME
} else {
    Join-Path $env:LOCALAPPDATA 'Android\Sdk'
}

$buildToolsRoot = Join-Path $sdk 'build-tools'
$buildToolDir = Get-ChildItem -LiteralPath $buildToolsRoot -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'aapt2.exe') } |
    Sort-Object { [version]$_.Name } -Descending |
    Select-Object -First 1

$platformDir = Get-ChildItem -LiteralPath (Join-Path $sdk 'platforms') -Directory |
    Where-Object { $_.Name -match '^android-\d+(?:\.\d+)?$' } |
    Sort-Object { [int](($_.Name -replace '^android-(\d+).*', '$1')) } -Descending |
    Select-Object -First 1

$javaHome = if ($env:JAVA_HOME) {
    $env:JAVA_HOME
} elseif (Test-Path 'C:\Program Files\Android\Android Studio\jbr') {
    'C:\Program Files\Android\Android Studio\jbr'
} else {
    $null
}

foreach ($path in @($app, $buildToolDir.FullName, $platformDir.FullName, $javaHome)) {
    if (!$path -or !(Test-Path -LiteralPath $path)) {
        throw "Required Android SDK/JDK path was not found: $path"
    }
}

$aapt2 = Join-Path $buildToolDir.FullName 'aapt2.exe'
$d8 = Join-Path $buildToolDir.FullName 'd8.bat'
$zipalign = Join-Path $buildToolDir.FullName 'zipalign.exe'
$platform = Join-Path $platformDir.FullName 'android.jar'
$javac = Join-Path $javaHome 'bin\javac.exe'
$jar = Join-Path $javaHome 'bin\jar.exe'

foreach ($path in @($aapt2, $d8, $zipalign, $platform, $javac, $jar)) {
    if (!(Test-Path -LiteralPath $path)) {
        throw "Required build tool was not found: $path"
    }
}

if (Test-Path -LiteralPath $out) {
    Get-ChildItem -LiteralPath $out -Force | Remove-Item -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $classes, $dex, $generated | Out-Null

$env:JAVA_HOME = $javaHome
$env:PATH = "$($javaHome)\bin;$env:PATH"
$resources = Join-Path $out 'resources.zip'
$manifest = Join-Path $app 'src\main\AndroidManifest.xml'

& $aapt2 compile --dir (Join-Path $app 'src\main\res') -o $resources
if ($LASTEXITCODE -ne 0) { throw 'aapt2 resource compilation failed' }

& $aapt2 link -o (Join-Path $out 'unsigned.apk') -I $platform `
    --manifest $manifest --java $generated --auto-add-overlay `
    --min-sdk-version 23 --target-sdk-version 35 $resources
if ($LASTEXITCODE -ne 0) { throw 'aapt2 resource linking failed' }

$sources = @(Get-ChildItem -LiteralPath $generated -Recurse -Filter '*.java' |
    Select-Object -ExpandProperty FullName)
$sources += Join-Path $app 'src\main\java\com\urlsteam\tv\MainActivity.java'
& $javac -source 8 -target 8 -classpath $platform -d $classes $sources
if ($LASTEXITCODE -ne 0) { throw 'javac compilation failed' }

$classFiles = @(Get-ChildItem -LiteralPath $classes -Recurse -Filter '*.class' |
    Select-Object -ExpandProperty FullName)
& $d8 --lib $platform --output $dex $classFiles
if ($LASTEXITCODE -ne 0) { throw 'D8 dex compilation failed' }

& $aapt2 link -o (Join-Path $out 'final-unsigned.apk') -I $platform `
    --manifest $manifest --auto-add-overlay `
    --min-sdk-version 23 --target-sdk-version 35 $resources
if ($LASTEXITCODE -ne 0) { throw 'final APK resource linking failed' }

& $jar uf (Join-Path $out 'final-unsigned.apk') -C $dex classes.dex
if ($LASTEXITCODE -ne 0) { throw 'classes.dex merge failed' }

& $zipalign -f 4 (Join-Path $out 'final-unsigned.apk') `
    (Join-Path $out 'urlsteam-tv-aligned.apk')
if ($LASTEXITCODE -ne 0) { throw 'APK zip alignment failed' }

Write-Output (Join-Path $out 'urlsteam-tv-aligned.apk')
