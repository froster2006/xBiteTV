"""HTTP API for discovering observable video URLs."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, request

try:
    from .extract_video_url import discover_source_links, find_video_urls, find_video_urls_from_index
    from .match_links import find_matches_from_sources
except ImportError:  # pragma: no cover - allows running this file directly with python src/api.py
    from extract_video_url import discover_source_links, find_video_urls, find_video_urls_from_index
    from match_links import find_matches_from_sources


MAX_URL_LENGTH = 2_048
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
MAX_JOB_SOURCES = 100
MAX_JOB_RESULTS = 10
MAX_HISTORY_URLS = 50
VIDEO_CACHE_TTL_SECONDS = 600
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
active_jobs: dict[str, str] = {}
video_cache: dict[str, dict] = {}
history_urls: list[str] = []
history_lock = threading.Lock()
job_executor = ThreadPoolExecutor(max_workers=8)
GAMES_FILE = Path(__file__).resolve().parents[1] / "games.json"


def _validate_target_url(value: str | None) -> str:
    if not value:
        raise ValueError("query parameter 'url' is required")
    if len(value) > MAX_URL_LENGTH:
        raise ValueError("url is too long")

    parsed = urlparse(value)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError("url must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not accepted")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("url contains an invalid port") from exc
    if port not in ALLOWED_PORTS:
        raise ValueError("only HTTP and HTTPS ports are accepted")

    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("hostname could not be resolved") from exc

    for address in addresses:
        resolved_ip = ipaddress.ip_address(address[4][0])
        if (
            resolved_ip.is_private
            or resolved_ip.is_loopback
            or resolved_ip.is_link_local
            or resolved_ip.is_multicast
            or resolved_ip.is_reserved
            or resolved_ip.is_unspecified
        ):
            raise ValueError("private or local network targets are not accepted")

    return value


def _remember_url(value: str) -> None:
    with history_lock:
        if value in history_urls:
            history_urls.remove(value)
        history_urls.insert(0, value)
        del history_urls[MAX_HISTORY_URLS:]


def _run_source_job(job_id: str, index_url: str) -> None:
    try:
        app.logger.info("job %s checking submitted URL directly: %s", job_id, index_url)
        try:
            direct_candidates = find_video_urls(index_url)
        except Exception:
            app.logger.exception("job %s direct check failed: %s", job_id, index_url)
            direct_candidates = []
        with jobs_lock:
            job = jobs[job_id]
            for candidate in direct_candidates[:MAX_JOB_RESULTS]:
                app.logger.info("job %s direct video candidate found: %s", job_id, candidate.url)
                if candidate.url not in job["video_urls"]:
                    job["videos"].append(asdict(candidate))
                    job["video_urls"].add(candidate.url)
            job["completed"] = 1
            job["total"] = 1
            if job["videos"]:
                job["status"] = "complete"
                return

        app.logger.info("job %s found no direct video; checking table sources", job_id)
        source_urls = discover_source_links(index_url)[:MAX_JOB_SOURCES]
        app.logger.info("job %s discovered %d source link(s) from %s", job_id, len(source_urls), index_url)
        for number, source_url in enumerate(source_urls, 1):
            app.logger.info("job %s source %d discovered: %s", job_id, number, source_url)
            print(f"[debug] job {job_id} source {number}/{len(source_urls)} checking: {source_url}", flush=True)
        with jobs_lock:
            jobs[job_id]["total"] = len(source_urls)

        futures = {}
        for number, source_url in enumerate(source_urls, 1):
            try:
                validated_url = _validate_target_url(source_url)
            except ValueError:
                app.logger.info("job %s skipped invalid source: %s", job_id, source_url)
                print(f"[debug] job {job_id} source {number}/{len(source_urls)} skipped invalid: {source_url}", flush=True)
                with jobs_lock:
                    jobs[job_id]["completed"] += 1
                continue
            app.logger.info("job %s checking source: %s", job_id, validated_url)
            print(f"[debug] job {job_id} source {number}/{len(source_urls)} running: {validated_url}", flush=True)
            futures[job_executor.submit(find_video_urls, validated_url)] = source_url
        for future in as_completed(futures):
            try:
                candidates = future.result()
            except Exception:
                app.logger.exception("job %s failed checking source: %s", job_id, futures[future])
                candidates = []
            with jobs_lock:
                job = jobs[job_id]
                remaining = MAX_JOB_RESULTS - len(job["videos"])
                for candidate in candidates[:remaining]:
                    app.logger.info("job %s video candidate found: %s", job_id, candidate.url)
                    if candidate.url not in job["video_urls"]:
                        job["videos"].append(asdict(candidate))
                        job["video_urls"].add(candidate.url)
                job["completed"] += 1
                app.logger.info(
                    "job %s checked source %s; found %d candidate(s), %d total result(s)",
                    job_id,
                    futures[future],
                    len(candidates),
                    len(job["videos"]),
                )
                if len(job["videos"]) >= MAX_JOB_RESULTS:
                    app.logger.info("job %s reached the %d-result limit", job_id, MAX_JOB_RESULTS)
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    break
        with jobs_lock:
            jobs[job_id]["status"] = "complete"
    except Exception:
        app.logger.exception("job %s failed during source discovery", job_id)
        with jobs_lock:
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = "source discovery failed"
    finally:
        with jobs_lock:
            job = jobs[job_id]
            job.pop("video_urls", None)
            if active_jobs.get(index_url) == job_id:
                active_jobs.pop(index_url, None)
            if job["status"] == "complete" and job["videos"]:
                video_cache[index_url] = {
                    "created": time.monotonic(),
                    "videos": list(job["videos"]),
                }
                app.logger.info("cached %d result(s) for %s", len(job["videos"]), index_url)


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout, force=True)
    app = Flask(__name__, static_folder="..", static_url_path="/web")
    app.logger.setLevel(logging.INFO)
    if not app.logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        app.logger.addHandler(handler)
    app.logger.propagate = False

    @app.get("/")
    def index():
        return app.send_static_file("web/index.html")

    @app.get("/game.html")
    def game_page():
        return app.send_static_file("web/game.html")

    @app.get("/input.html")
    def input_page():
        return app.send_static_file("web/input.html")

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get("/games")
    def get_games() -> tuple[object, int]:
        try:
            matches = find_matches_from_sources()
        except Exception as exc:
            app.logger.exception("failed to load live matches")
            return {"error": f"failed to load live matches: {exc}"}, 500

        games = [
            {
                "match_name": f"{match.team_a} vs {match.team_b}",
                "status": match.status,
                "link": match.url,
                "source_web": match.source_web,
                "source_prefix": match.source_prefix,
            }
            for match in matches
        ]
        return games, 200

    @app.get("/history")
    def get_history() -> tuple[dict, int]:
        with history_lock:
            return {"urls": list(history_urls)}, 200

    @app.post("/history")
    def add_history_url() -> tuple[dict, int]:
        payload = request.get_json(silent=True) or {}
        try:
            target_url = _validate_target_url(payload.get("url"))
        except (AttributeError, ValueError) as exc:
            return {"error": str(exc)}, 400
        _remember_url(target_url)
        with history_lock:
            return {"urls": list(history_urls)}, 201

    @app.delete("/history")
    def delete_history_url() -> tuple[dict, int]:
        value = request.args.get("url")
        with history_lock:
            if value in history_urls:
                history_urls.remove(value)
        return {"urls": list(history_urls)}, 200

    @app.post("/jobs")
    def create_job() -> tuple[dict, int]:
        payload = request.get_json(silent=True) or {}
        try:
            target_url = _validate_target_url(payload.get("url"))
        except (AttributeError, ValueError) as exc:
            return {"error": str(exc)}, 400

        _remember_url(target_url)
        with jobs_lock:
            now = time.monotonic()
            cached = video_cache.get(target_url)
            if cached and now - cached["created"] < VIDEO_CACHE_TTL_SECONDS:
                job_id = uuid.uuid4().hex
                app.logger.info("cache hit for %s", target_url)
                jobs[job_id] = {
                    "status": "complete",
                    "cached": True,
                    "url": target_url,
                    "videos": list(cached["videos"]),
                    "completed": 1,
                    "total": 1,
                }
                return {"job_id": job_id, "status": "complete", "cached": True}, 202
            if cached:
                app.logger.info("expired cache entry for %s", target_url)
                video_cache.pop(target_url, None)

            existing_job_id = active_jobs.get(target_url)
            if existing_job_id and jobs.get(existing_job_id, {}).get("status") == "running":
                return {"job_id": existing_job_id, "status": "running", "deduplicated": True}, 202

            job_id = uuid.uuid4().hex
            active_jobs[target_url] = job_id
            jobs[job_id] = {
                "status": "running",
                "cached": False,
                "url": target_url,
                "videos": [],
                "video_urls": set(),
                "completed": 0,
                "total": None,
            }
        job_executor.submit(_run_source_job, job_id, target_url)
        return {"job_id": job_id, "status": "running"}, 202

    @app.get("/jobs/<job_id>")
    def get_job(job_id: str) -> tuple[dict, int]:
        with jobs_lock:
            job = jobs.get(job_id)
            if job is None:
                return {"error": "job not found"}, 404
            response = {key: value for key, value in job.items() if key != "video_urls"}
        return response, 200

    @app.get("/videos")
    def videos() -> tuple[dict, int]:
        try:
            target_url = _validate_target_url(request.args.get("url"))
        except ValueError as exc:
            return {"error": str(exc)}, 400

        with jobs_lock:
            cached = video_cache.get(target_url)
            if cached and time.monotonic() - cached["created"] < VIDEO_CACHE_TTL_SECONDS:
                app.logger.info("serving cached video results for %s", target_url)
                return {"url": target_url, "videos": list(cached["videos"]), "cached": True}, 200
            if cached:
                video_cache.pop(target_url, None)

        try:
            mode = request.args.get("mode", "auto").lower()
            if mode == "sources":
                candidates = find_video_urls_from_index(
                    target_url,
                    max_results=MAX_JOB_RESULTS,
                    validate_url=_validate_target_url,
                )
            elif mode == "direct":
                candidates = find_video_urls(target_url)
            elif mode == "auto":
                candidates = find_video_urls(target_url)
                if not candidates:
                    candidates = find_video_urls_from_index(
                        target_url,
                        max_results=MAX_JOB_RESULTS,
                        validate_url=_validate_target_url,
                    )
            else:
                return {"error": "mode must be 'auto', 'direct', or 'sources'"}, 400
        except Exception:
            app.logger.exception("video URL discovery failed for %s", target_url)
            return {"error": "video URL discovery failed"}, 502

        if candidates:
            with jobs_lock:
                video_cache[target_url] = {
                    "created": time.monotonic(),
                    "videos": [asdict(candidate) for candidate in candidates[:MAX_JOB_RESULTS]],
                }

        return {
            "url": target_url,
            "videos": [asdict(candidate) for candidate in candidates[:MAX_JOB_RESULTS]],
        }, 200

    @app.errorhandler(404)
    def not_found(_error) -> tuple:
        return jsonify(error="endpoint not found"), 404

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
