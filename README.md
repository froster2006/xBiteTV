# xBiteTV

## Overview

The purpose of this project is to watch authorized games from Footybite or NBAbite on a TV. Deploy the Python/Playwright backend to a server that can reach the target sites. The frontend has two parts: `web/input.html` is intended for use on a cellphone to enter game URLs, and the TV app displays those saved URLs so you can select one and play its available video sources. The TV and phone should access the same deployed backend.

urlSteam is a Python/Playwright service that discovers observable video URLs from authorized video pages and presents them in a simple browser player.

## Local setup

Create and activate a virtual environment, then install Python and browser dependencies:

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
playwright install chromium
```

## Start the backend and web UI

Start Flask from the repository root:

```powershell
python -m src.api
```

Open the web interface at [http://127.0.0.1:5000/](http://127.0.0.1:5000/). Enter an authorized page URL; the UI checks the page directly, then checks source-table links and embedded players. Results appear as source buttons as background discovery completes.

The API also exposes:

```text
GET  /health
GET  /videos?url=<encoded-url>
POST /jobs              {"url": "<page-url>"}
GET  /jobs/<job-id>
GET  /history
DELETE /history?url=<encoded-url>
```

## Deploy the backend to a NAS with Docker

Build and publish the image from the project root:

```powershell
docker login
docker build -t YOUR_DOCKERHUB_USERNAME/urlsteam:latest .
docker push YOUR_DOCKERHUB_USERNAME/urlsteam:latest
```

Replace the image name in `compose.yaml` with the image you pushed. Copy `compose.yaml` to the NAS, then deploy:

```bash
docker compose pull
docker compose up -d
```

The compose file maps NAS port `7666` to the container’s Flask port `5000`. The phone URL manager is available at `http://NAS_IP:7666/input.html`, and the browser player is at `http://NAS_IP:7666/`. URL history is stored in memory and resets when the container restarts.

## Build the Google TV APK

Before building, manually edit `tv-app/app/src/main/java/com/urlsteam/tv/MainActivity.java` and set `START_URL` to the deployed NAS address:

```java
private static final String START_URL = "http://NAS_IP:7666/";
```

Do not use `127.0.0.1` for a physical TV; that refers to the TV itself. From the repository root, build the debug APK with the included PowerShell script:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\build.ps1
```

The aligned APK is created at `tv-app/out/urlsteam-tv-aligned.apk`. To sign it with the local debug key and install it through ADB:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\build-and-install.ps1
```

The TV and NAS must be on the same network. Rebuild the APK whenever the NAS address changes.

Only use the service with sites and media you are authorized to access. Do not use it to bypass DRM, paywalls, authentication, or access controls.
