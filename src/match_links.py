"""Discover match-page links from supported sports index pages."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunsplit
from urllib.parse import unquote

from playwright.sync_api import sync_playwright


MATCH_PATH_RE = re.compile(r"^/[^/]+-vs-[^/]+/\d+/?$", re.IGNORECASE)
GAMES_FILE = Path(__file__).resolve().parents[1] / "games.json"
SOURCES_FILE = Path(__file__).resolve().parents[1] / "sources.json"


def parse_status_window_hours(status: str | None) -> float | None:
    """Return the number of hours in a human-readable match status, if it is within hours.

    Examples:
      - "Live Now" -> 0
      - "Starts in 1 hours 5 mins" -> 1.083333...
      - "Starts in 1 day 2 hours" -> None
    """
    text = re.sub(r"\s+", " ", status or "").strip().lower()
    if not text:
        return None

    if re.search(r"\b(live(?:\s+now)?|started|in progress)\b", text):
        return 0.0

    if re.search(r"\bday(s)?\b", text):
        return None

    total_minutes = 0
    for amount, unit in re.findall(r"(\d+)\s*(hour|hr|hrs|hours|minute|min|mins|minutes)", text):
        value = int(amount)
        if "hour" in unit:
            total_minutes += value * 60
        else:
            total_minutes += value

    if total_minutes == 0:
        return None
    return total_minutes / 60


def normalize_match_status(status: str | None, *, max_hours: int | float | None = None) -> str | None:
    """Keep live matches marked as started while preserving upcoming countdown text."""
    text = re.sub(r"\s+", " ", status or "").strip()
    if not text:
        return None

    if re.search(r"\b(live|started|in progress|live now)\b", text, re.IGNORECASE):
        return "started"

    hours = parse_status_window_hours(text)
    if hours is None:
        return None
    if max_hours is not None and hours > float(max_hours):
        return None
    return text


@dataclass(frozen=True)
class MatchInfo:
    url: str
    team_a: str
    team_b: str
    status: str
    source_web: str
    source_prefix: str = ""


def _team_names(path: str) -> tuple[str, str]:
    slug = unquote(path).strip("/").rsplit("/", 1)[0]
    team_a, team_b = re.split(r"-vs-", slug, maxsplit=1, flags=re.IGNORECASE)
    return team_a.replace("-", " ").title(), team_b.replace("-", " ").title()


def _is_match_url_for_hosts(url: str, allowed_hosts: set[str]) -> bool:
    parsed = urlparse(url)
    hostname = parsed.hostname.lower().rstrip(".") if parsed.hostname else None
    return hostname in allowed_hosts and bool(MATCH_PATH_RE.fullmatch(parsed.path))


def find_match_links(
    site_url: str,
    *,
    section_name: str = "Important Games",
    wait_ms: int = 1_500,
    allowed_hosts: list[str] | None = None,
) -> list[str]:
    """Return match links found only inside the requested page section.

    The function intentionally returns only links whose hostname matches the
    supplied index page. Query strings are preserved; fragments are removed.
    """
    base = urlparse(site_url)
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("site_url must be an absolute HTTP or HTTPS URL")
    match_hosts = {base.hostname.lower().rstrip(".")}
    match_hosts.update(host.lower().strip().rstrip(".") for host in allowed_hosts or [] if host.strip())

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(site_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait_ms)
            hrefs = page.evaluate(
                """
                sectionName => {
                    const wanted = sectionName.trim().toLowerCase();
                    const roots = new Set();
                    const headingSelector = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
                    for (const heading of document.querySelectorAll(headingSelector)) {
                        const text = (heading.innerText || heading.textContent || '').trim().toLowerCase();
                        if (text === wanted || text.includes(wanted)) {
                            if (heading.parentElement) roots.add(heading.parentElement);
                            if (heading.parentElement?.parentElement) roots.add(heading.parentElement.parentElement);
                        }
                    }
                    for (const element of document.querySelectorAll('[class*="important" i], [id*="important" i]')) {
                        roots.add(element);
                    }
                    return [...roots].flatMap(root =>
                        [...root.querySelectorAll('a[href]')].map(anchor => anchor.href)
                    ).filter(Boolean);
                }
                """,
                section_name,
            )
        finally:
            browser.close()

    links: set[str] = set()
    for href in hrefs:
        absolute = urljoin(site_url, href)
        parsed = urlparse(absolute)
        if not _is_match_url_for_hosts(absolute, match_hosts):
            continue
        links.add(urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.query, "")))

    return sorted(links)


def find_matches(
    site_url: str,
    *,
    section_name: str = "Important Games",
    wait_ms: int = 1_500,
    max_hours: int | float | None = None,
    source_prefix: str | None = None,
    allowed_hosts: list[str] | None = None,
) -> list[MatchInfo]:
    """Return match details and visible status text from the requested section."""
    base = urlparse(site_url)
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("site_url must be an absolute HTTP or HTTPS URL")
    match_hosts = {base.hostname.lower().rstrip(".")}
    match_hosts.update(host.lower().strip().rstrip(".") for host in allowed_hosts or [] if host.strip())

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(site_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait_ms)
            entries = page.evaluate(
                """
                sectionName => {
                    const wanted = (sectionName || '').trim().toLowerCase();
                    const roots = new Set();
                    if (wanted) {
                        const selector = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
                        for (const heading of document.querySelectorAll(selector)) {
                            const text = (heading.innerText || heading.textContent || '').trim().toLowerCase();
                            if (text === wanted || text.includes(wanted)) {
                                if (heading.parentElement) roots.add(heading.parentElement);
                                if (heading.parentElement?.parentElement) roots.add(heading.parentElement.parentElement);
                            }
                        }
                        for (const element of document.querySelectorAll('[class*="important" i], [id*="important" i]')) {
                            roots.add(element);
                        }
                    } else {
                        roots.add(document.body);
                    }
                    const output = [];
                    for (const root of roots) {
                        for (const anchor of root.querySelectorAll('a[href]')) {
                            const container = anchor.closest('article, li, tr, [class*="match" i], [class*="game" i]') || anchor.parentElement;
                            const statusNode = container?.querySelector('time, [class*="status" i], [class*="time" i]');
                            output.push({
                                href: anchor.href,
                                text: (container?.innerText || anchor.innerText || '').trim(),
                                status: (statusNode?.innerText || statusNode?.textContent || '').trim()
                            });
                        }
                    }
                    return output;
                }
                """,
                section_name,
            )
        finally:
            browser.close()

    matches: dict[str, MatchInfo] = {}
    for entry in entries:
        absolute = urljoin(site_url, entry["href"])
        parsed = urlparse(absolute)
        if not _is_match_url_for_hosts(absolute, match_hosts):
            continue
        clean_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.query, ""))
        team_a, team_b = _team_names(parsed.path)
        status = re.sub(r"\s+", " ", entry.get("status") or entry.get("text", "")).strip()
        normalized = normalize_match_status(status, max_hours=max_hours)
        if normalized is None:
            continue
        prefix = source_prefix or (base.hostname or "source")
        matches.setdefault(clean_url, MatchInfo(clean_url, team_a, team_b, normalized, site_url, prefix))

    return sorted(matches.values(), key=lambda match: match.url)


def load_sources_config(path: Path = SOURCES_FILE) -> list[dict[str, str | int | float | None | list[str]]]:
    """Load configured source sites from sources.json."""
    if not path.exists():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} contains invalid JSON") from exc

    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must contain a JSON list")

    sources: list[dict[str, str | int | float | None | list[str]]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        source_link = str(item.get("source_link", "")).strip()
        if not source_link:
            continue
        prefix = str(item.get("prefix", "")).strip() or (urlparse(source_link).hostname or "source")
        section_name = str(item.get("section_name", "Important Games")).strip()
        max_hours_value = item.get("max_hours", item.get("hours_limit", item.get("hours_to_start")))
        try:
            max_hours = float(max_hours_value) if max_hours_value is not None else None
        except (TypeError, ValueError):
            max_hours = None
        allowed_hosts_value = item.get("allowed_hosts", [])
        if isinstance(allowed_hosts_value, str):
            allowed_hosts_value = [allowed_hosts_value]
        if not isinstance(allowed_hosts_value, list):
            allowed_hosts_value = []
        allowed_hosts = [
            host
            for value in allowed_hosts_value
            if (host := str(value).strip().lower().rstrip("."))
        ]
        sources.append({
            "source_link": source_link.rstrip("/"),
            "prefix": prefix,
            "section_name": section_name,
            "max_hours": max_hours,
            "allowed_hosts": allowed_hosts,
        })
    return sources


def find_matches_from_sources(config_path: Path = SOURCES_FILE) -> list[MatchInfo]:
    """Return all match records from every configured source."""
    matches: dict[str, MatchInfo] = {}
    for source in load_sources_config(config_path):
        source_link = str(source["source_link"])
        section_name = str(source.get("section_name", "Important Games"))
        max_hours = source.get("max_hours")
        source_prefix = str(source["prefix"])
        allowed_hosts = source.get("allowed_hosts", [])
        for match in find_matches(
            source_link,
            section_name=section_name,
            max_hours=max_hours,
            source_prefix=source_prefix,
            allowed_hosts=allowed_hosts if isinstance(allowed_hosts, list) else [],
        ):
            matches.setdefault(match.url, match)
    return list(matches.values())


def save_matches(matches: list[MatchInfo], output_path: Path = GAMES_FILE) -> None:
    """Save matches in the public games.json format."""
    payload = [
        {
            "match_name": f"{match.team_a} vs {match.team_b}",
            "status": match.status,
            "link": match.url,
            "source_web": match.source_web,
            "source_prefix": match.source_prefix,
        }
        for match in matches
    ]
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find match links on configured sports index pages.")
    parser.add_argument(
        "site_url",
        nargs="?",
        default=None,
        help="optional single index page to scan; otherwise uses sources.json",
    )
    parser.add_argument(
        "--section",
        default="Important Games",
        help="section heading or marker to scan when scanning a single site",
    )
    parser.add_argument(
        "--output",
        default=str(GAMES_FILE),
        help="JSON output path",
    )
    args = parser.parse_args()

    if args.site_url:
        matches = find_matches(args.site_url, section_name=args.section)
    else:
        config = load_sources_config()
        if config:
            matches = find_matches_from_sources()
        else:
            matches = find_matches("https://footybite.futbol/", section_name="Important Games")

    output_path = Path(args.output)
    save_matches(matches, output_path)
    print(f"Found {len(matches)} match link(s); saved to {output_path}:")
    for match in matches:
        print(f"{match.team_a} vs {match.team_b} | {match.status} | {match.url}")


if __name__ == "__main__":
    main()
