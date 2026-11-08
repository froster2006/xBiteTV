"""Find observable, non-DRM video URLs on a page using Playwright."""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright


logger = logging.getLogger(__name__)
IGNORE_FILE = Path(__file__).resolve().parents[1] / "source_ignore.txt"


MEDIA_RE = re.compile(r"\.(?:m3u8|mpd|mp4|webm|m4v)(?:[?#]|$)", re.IGNORECASE)
MEDIA_TYPES = {
    "application/dash+xml",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "video/mp4",
    "video/webm",
}


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    content_type: str | None = None


def _ignored_fragments() -> list[str]:
    if not IGNORE_FILE.exists():
        return []
    return [
        line.strip().lower().rstrip("/")
        for line in IGNORE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _is_ignored_url(url: str) -> bool:
    lowered_url = url.lower()
    return any(fragment in lowered_url for fragment in _ignored_fragments())


def _rank_source_link(item: dict[str, str]) -> int:
    text = f"{item.get('text', '')} {item.get('url', '')}".lower()
    terms = ("source", "stream", "watch", "live", "link", "player", "embed")
    return sum(term in text for term in terms)


def discover_source_links(index_url: str) -> list[str]:
    """Return likely source-page links from a sports/event index page."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(index_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_500)
            raw_links = page.locator(
                "table a, table iframe, table [data-href], table [data-url], table [data-link], table [onclick]"
            ).evaluate_all(
                """nodes => nodes.map(node => ({
                    text: (node.innerText || node.title || '').trim(),
                    url: node.href || node.src || node.dataset.href || node.dataset.url ||
                         node.dataset.link || node.getAttribute('onclick') || ''
                }))"""
            )
            table_html = page.locator("table").evaluate_all("tables => tables.map(table => table.outerHTML)")
        finally:
            browser.close()

    links: dict[str, dict[str, str]] = {}
    for item in raw_links:
        absolute_url = urljoin(index_url, item.get("url", ""))
        parsed = urlparse(absolute_url)
        if (
            parsed.scheme in {"http", "https"}
            and absolute_url != index_url
            and not _is_ignored_url(absolute_url)
        ):
            links.setdefault(absolute_url, {"text": item.get("text", ""), "url": absolute_url})
    for table in table_html:
        for literal_url in re.findall(r"https?://[^\s\"'<>]+", html.unescape(table)):
            clean_url = literal_url.rstrip(").,;'")
            if clean_url != index_url and not _is_ignored_url(clean_url):
                links.setdefault(clean_url, {"text": "table link", "url": clean_url})
    return [item["url"] for item in sorted(links.values(), key=_rank_source_link, reverse=True)]


def _is_media_url(url: str, content_type: str | None = None) -> bool:
    normalized_type = (content_type or "").split(";", 1)[0].lower().strip()
    lowered_url = url.lower()
    path = urlparse(url).path.lower()
    return normalized_type in MEDIA_TYPES or bool(
        MEDIA_RE.search(path)
        or re.search(r"(?:m3u8|mpd|manifest|playlist|master|\.mp4|\.webm)", lowered_url)
    )


def _read_page_media(page: Page) -> list[Candidate]:
    candidates: list[Candidate] = []
    for frame in page.frames:
        try:
            values = frame.locator("video, video source, audio source").evaluate_all(
                """nodes => nodes.map(node => ({
                    url: node.currentSrc || node.src || node.getAttribute('src') || '',
                    type: node.getAttribute('type') || ''
                }))"""
            )
        except Exception:
            continue
        candidates.extend(
            Candidate(item["url"], "dom", item.get("type"))
            for item in values
            if item.get("url") and _is_media_url(item["url"], item.get("type"))
        )
    return candidates


def find_video_urls(
    target_url: str,
    *,
    headed: bool = False,
    wait_ms: int = 8_000,
    follow_iframes: bool = True,
) -> list[Candidate]:
    candidates: list[Candidate] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(
            service_workers="block",
            user_agent="Mozilla/5.0 (compatible; authorized-media-inspector/1.0)",
        )
        page = context.new_page()

        def record_response(response) -> None:
            content_type = response.headers.get("content-type")
            if _is_media_url(response.url, content_type):
                candidates.append(Candidate(response.url, "network", content_type))
                return
            if response.request.resource_type not in {"document", "xhr", "fetch"}:
                return
            try:
                body_start = response.body()[:8_192]
            except Exception:
                return
            if b"#EXTM3U" in body_start or b"<MPD" in body_start or b"<mpd" in body_start:
                candidates.append(Candidate(response.url, "manifest", content_type))

        def record_request(request) -> None:
            if request.resource_type in {"media", "xhr", "fetch"} and _is_media_url(request.url):
                candidates.append(Candidate(request.url, "request"))

        page.on("response", record_response)
        page.on("request", record_request)
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=30_000)
            for frame in page.frames:
                try:
                    frame.locator(
                        "button[aria-label*='Play' i], .play, .play-button, .vjs-big-play-button"
                    ).first.click(timeout=500)
                except Exception:
                    pass
                try:
                    frame.locator("video").evaluate_all(
                        "videos => videos.forEach(video => video.play().catch(() => {}))"
                    )
                except Exception:
                    pass
            page.wait_for_timeout(wait_ms)
            candidates.extend(_read_page_media(page))
            for resource_url in page.evaluate("() => performance.getEntriesByType('resource').map(entry => entry.name)"):
                if _is_media_url(resource_url):
                    candidates.append(Candidate(resource_url, "resource"))
            if follow_iframes:
                embedded_urls = {
                    frame.url
                    for frame in page.frames
                    if frame != page.main_frame and frame.url.startswith(("http://", "https://"))
                }
                try:
                    dom_iframe_urls = page.locator("iframe[src]").evaluate_all(
                        "iframes => iframes.map(iframe => iframe.src).filter(Boolean)"
                    )
                    embedded_urls.update(
                        iframe_url
                        for iframe_url in dom_iframe_urls
                        if iframe_url.startswith(("http://", "https://"))
                    )
                except Exception:
                    pass
                for embedded_url in sorted(embedded_urls):
                    logger.info("iframe source discovered: %s", embedded_url)
                for embedded_url in embedded_urls:
                    iframe_page = context.new_page()
                    try:
                        iframe_page.on("response", record_response)
                        iframe_page.on("request", record_request)
                        iframe_page.goto(embedded_url, wait_until="domcontentloaded", timeout=30_000)
                        try:
                            iframe_page.locator(
                                "button[aria-label*='Play' i], .play, .play-button, .vjs-big-play-button"
                            ).first.click(timeout=500)
                        except Exception:
                            pass
                        try:
                            iframe_page.locator("video").evaluate_all(
                                "videos => videos.forEach(video => video.play().catch(() => {}))"
                            )
                        except Exception:
                            pass
                        iframe_page.wait_for_timeout(wait_ms)
                        candidates.extend(
                            Candidate(candidate.url, "iframe")
                            for candidate in _read_page_media(iframe_page)
                        )
                        for resource_url in iframe_page.evaluate(
                            "() => performance.getEntriesByType('resource').map(entry => entry.name)"
                        ):
                            if _is_media_url(resource_url):
                                candidates.append(Candidate(resource_url, "iframe-resource"))
                    except Exception:
                        pass
                    finally:
                        iframe_page.close()
        except PlaywrightTimeoutError:
            # A slow page may still have produced useful response events.
            candidates.extend(_read_page_media(page))
        finally:
            context.close()
            browser.close()

    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        if not _is_ignored_url(candidate.url):
            unique.setdefault(candidate.url, candidate)
    return list(unique.values())


def find_video_urls_from_index(
    index_url: str,
    *,
    max_results: int | None = None,
    validate_url: Callable[[str], str] | None = None,
) -> list[Candidate]:
    """Discover source links on an index page, then inspect them for media URLs."""
    if max_results is not None and max_results < 1:
        raise ValueError("max_results must be at least 1")

    ordered_links = [{"url": link} for link in discover_source_links(index_url)]
    results: dict[str, Candidate] = {}
    for link in ordered_links:
        try:
            child_url = validate_url(link["url"]) if validate_url else link["url"]
            for candidate in find_video_urls(child_url):
                results.setdefault(candidate.url, candidate)
                if max_results is not None and len(results) >= max_results:
                    return list(results.values())
        except (ValueError, PlaywrightTimeoutError):
            continue

    return list(results.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="authorized page URL to inspect")
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--wait-ms", type=int, default=8_000, help="time to observe network activity")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of plain URLs")
    args = parser.parse_args()

    results = find_video_urls(args.url, headed=args.headed, wait_ms=args.wait_ms)
    if args.json:
        print(json.dumps([candidate.__dict__ for candidate in results], indent=2))
    else:
        for candidate in results:
            print(f"{candidate.url}\t[{candidate.source}]")

    if not results:
        raise SystemExit("No observable media URL found; the page may require authorized interaction or use protected media.")
