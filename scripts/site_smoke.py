"""Crawl local production output and fail on broken same-origin asset links."""

from __future__ import annotations

import argparse
import json
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
            if key in {"data-json-url", "href", "src"} and value:
                self.links.append(value)


def local_path(value: str, page: Path) -> Path | None:
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or value.startswith("#"):
        return None
    if parsed.path.startswith("/"):
        return OUTPUT_DIR / parsed.path.lstrip("/")
    return page.parent / parsed.path


def main(report_path: Path | None = None) -> None:
    issues = WebsiteService().validate_public_output()
    pages = sorted(OUTPUT_DIR.rglob("*.html"))
    if not pages:
        issues.append("no generated HTML pages found")

    for page in pages:
        parser = AssetParser()
        parser.feed(page.read_text(encoding="utf-8"))
        for value in parser.links:
            target = local_path(value, page)
            if not target:
                continue
            try:
                target.resolve().relative_to(OUTPUT_DIR.resolve())
            except ValueError:
                issues.append(
                    f"{page.relative_to(OUTPUT_DIR)}: path escapes output: {value}"
                )
                continue
            if not target.exists():
                issues.append(f"{page.relative_to(OUTPUT_DIR)}: missing {value}")
                continue
            if target.suffix == ".json":
                try:
                    json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    issues.append(
                        f"{page.relative_to(OUTPUT_DIR)}: invalid JSON {value}: {error}"
                    )

    if issues:
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {"format": 1, "ok": False, "pages": len(pages), "issues": issues},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        raise SystemExit("Site smoke check failed:\n- " + "\n- ".join(issues))
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                {"format": 1, "ok": True, "pages": len(pages), "issues": []},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"Site smoke check passed for {len(pages)} page(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, dest="report_path")
    arguments = parser.parse_args()
    main(arguments.report_path)
