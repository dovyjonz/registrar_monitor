"""Crawl local production output and fail on broken same-origin asset links."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from registrarmonitor.services.website_service import WebsiteService
from registrarmonitor.website.config import OUTPUT_DIR


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key in {"href", "src"} and value:
                self.links.append(value)


def local_path(value: str, page: Path) -> Path | None:
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or value.startswith("#"):
        return None
    if parsed.path.startswith("/"):
        return OUTPUT_DIR / parsed.path.lstrip("/")
    return page.parent / parsed.path


def main() -> None:
    issues = WebsiteService().validate_public_output()
    pages = sorted(OUTPUT_DIR.rglob("*.html"))
    if not pages:
        issues.append("no generated HTML pages found")

    for page in pages:
        parser = AssetParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for value in parser.links:
            target = local_path(value, page)
            if target and not target.exists():
                issues.append(f"{page.relative_to(OUTPUT_DIR)}: missing {value}")

    if issues:
        raise SystemExit("Site smoke check failed:\n- " + "\n- ".join(issues))
    print(f"Site smoke check passed for {len(pages)} page(s).")


if __name__ == "__main__":
    main()
