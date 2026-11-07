# urlSteam TV

This Android TV debug app wraps the local web UI at `http://192.168.50.88:5000/` in a WebView.

## Build with the repository PowerShell scripts

From the repository root:

```powershell
.\build.ps1
```

This creates an unsigned, aligned APK at `tv-app/out/urlsteam-tv-aligned.apk`.

To sign with a local debug key and install through ADB:

```powershell
.\build-and-install.ps1
```

Use `-SetAsHome` only if you intentionally want the app to become the TV launcher:

```powershell
.\build-and-install.ps1 -SetAsHome
```

## Build with Android Studio

1. Open the `tv-app` directory in Android Studio.
2. Let Gradle sync and install the requested Android SDK/API 35 components.
3. Select the `app` configuration.
4. Build the debug APK with **Build > Build APK(s)**.

The APK is created at:

```text
app/build/outputs/apk/debug/app-debug.apk
```

Install it on a Google TV device with USB debugging enabled:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

For a physical TV, `127.0.0.1` means the TV itself. If Flask runs on another computer, change `START_URL` in `app/src/main/java/com/urlsteam/tv/MainActivity.java` to that computer’s LAN address. For an Android emulator, use `10.0.2.2` to reach the host computer.
