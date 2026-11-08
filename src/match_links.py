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


@dataclass(frozen=True)
class MatchInfo:
    url: str
    team_a: str
    team_b: str
    status: str
    source_web: str


def _team_names(path: str) -> tuple[str, str]:
    slug = unquote(path).strip("/").rsplit("/", 1)[0]
    team_a, team_b = re.split(r"-vs-", slug, maxsplit=1, flags=re.IGNORECASE)
    return team_a.replace("-", " ").title(), team_b.replace("-", " ").title()


def find_match_links(
    site_url: str,
    *,
    section_name: str = "Important Games",
    wait_ms: int = 1_500,
) -> list[str]:
    """Return match links found only inside the requested page section.

    The function intentionally returns only links whose hostname matches the
    supplied index page. Query strings are preserved; fragments are removed.
    """
    base = urlparse(site_url)
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("site_url must be an absolute HTTP or HTTPS URL")

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
        if parsed.hostname != base.hostname:
            continue
        if not MATCH_PATH_RE.fullmatch(parsed.path):
            continue
        links.add(urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.query, "")))

    return sorted(links)


def find_matches(
    site_url: str,
    *,
    section_name: str = "Important Games",
    wait_ms: int = 1_500,
) -> list[MatchInfo]:
    """Return match details and visible status text from the requested section."""
    base = urlparse(site_url)
    if base.scheme not in {"http", "https"} or not base.hostname:
        raise ValueError("site_url must be an absolute HTTP or HTTPS URL")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(site_url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(wait_ms)
            entries = page.evaluate(
                """
                sectionName => {
                    const wanted = sectionName.trim().toLowerCase();
                    const roots = new Set();
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
        if parsed.hostname != base.hostname or not MATCH_PATH_RE.fullmatch(parsed.path):
            continue
        clean_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.query, ""))
        team_a, team_b = _team_names(parsed.path)
        status = re.sub(r"\s+", " ", entry.get("status") or entry.get("text", "")).strip()
        if re.search(r"\b(live|started|in progress)\b", status, re.IGNORECASE):
            status = "started"
        matches.setdefault(clean_url, MatchInfo(clean_url, team_a, team_b, status, site_url))

    return sorted(matches.values(), key=lambda match: match.url)


def save_matches(matches: list[MatchInfo], output_path: Path = GAMES_FILE) -> None:
    """Save matches in the public games.json format."""
    payload = [
        {
            "match_name": f"{match.team_a} vs {match.team_b}",
            "status": match.status,
            "link": match.url,
            "source_web": match.source_web,
        }
        for match in matches
    ]
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find match links on a sports index page.")
    parser.add_argument(
        "site_url",
        nargs="?",
        default="https://footybite.futbol/",
        help="index page to scan",
    )
    parser.add_argument(
        "--section",
        default="Important Games",
        help="section heading or marker to scan",
    )
    parser.add_argument(
        "--output",
        default=str(GAMES_FILE),
        help="JSON output path",
    )
    args = parser.parse_args()

    matches = find_matches(args.site_url, section_name=args.section)
    output_path = Path(args.output)
    save_matches(matches, output_path)
    print(f"Found {len(matches)} match link(s); saved to {output_path}:")
    for match in matches:
        print(f"{match.team_a} vs {match.team_b} | {match.status} | {match.url}")


if __name__ == "__main__":
    main()
