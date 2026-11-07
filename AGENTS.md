# Repository Guidelines

## Project Structure & Module Organization

This project is a Python utility that uses Playwright’s synchronous API to load an authorized target site and identify the underlying video URL for playback in a separate web page. Keep application code in `src/` (or a small top-level module while the project is minimal), tests in `tests/`, and local web-page assets in `assets/` or `web/`. Keep extracted URLs and browser-session data out of version control.

## Build, Test, and Development Commands

Create a virtual environment, then install dependencies with `python -m pip install -r requirements.txt`. Install the required browser with `playwright install`. Run the extractor with `python -m src` (or the project’s eventual console entry point), and run tests with `python -m pytest`. Keep command-line options for the target URL, headed/headless mode, and output format explicit rather than hard-coded.

## Coding Style & Naming Conventions

Follow PEP 8 and the formatter/linter configured by the project. Use four spaces for indentation, `snake_case` for modules, functions, and variables, and `PascalCase` for classes. Keep Playwright page interaction separate from URL parsing and output formatting. Prefer explicit waits and narrowly scoped locators over arbitrary sleeps.

## Testing Guidelines

Use `pytest` for unit tests and mock browser/page responses where possible. Name files `test_*.py` and test functions `test_*`. Cover URL extraction, redirects, missing media, malformed input, and timeout behavior. Keep live-site tests opt-in because external pages are nondeterministic and may change without notice.

## Commit & Pull Request Guidelines

Use short, imperative commit subjects such as `Add video URL extraction`. Keep unrelated changes separate. Pull requests should explain the behavior change, mention validation performed, link any relevant issue, and include reproduction steps or screenshots when the local playback page changes.

## Security & Configuration Tips

Never commit credentials, tokens, private keys, cookies, browser profiles, or local environment files. Use `.env.example` for configuration documentation. Only inspect sites and media the project is authorized to access; respect terms, robots/rate limits, copyright, authentication boundaries, and DRM. Do not attempt to defeat DRM, paywalls, access controls, or anti-bot protections. Validate target URLs and avoid passing untrusted URLs to privileged browser contexts.
