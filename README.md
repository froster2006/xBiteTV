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

## Docker

Build and publish the image:

```powershell
docker build -t YOUR_DOCKERHUB_USERNAME/urlsteam:latest .
docker push YOUR_DOCKERHUB_USERNAME/urlsteam:latest
```

Replace the image name in compose.yaml, then deploy on the NAS:

```bash
docker compose pull
docker compose up -d
```

Open port 7666 on the NAS. URL history is stored in memory and resets when the service restarts.

Only use the service with sites and media you are authorized to access. Do not use it to bypass DRM, paywalls, authentication, or access controls.
