#!/usr/bin/env python3
"""
Generate HTML website for visualizing course enrollment data.
(Wrapper around registrarmonitor.services.website_service)

Usage:
    python generate_website.py [options]
"""

import argparse
import sys
from pathlib import Path

# Add src to path so we can import the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from registrarmonitor.services.website_service import WebsiteService
    from registrarmonitor.website.config import SEMESTER_MAP
    from registrarmonitor.config import get_config
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate HTML website for enrollment data visualization"
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy to Cloudflare Pages after generating HTML",
    )
    parser.add_argument(
        "--semester",
        choices=list(SEMESTER_MAP.keys()),
        help="Generate only a specific semester (ignores checksums)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force regenerate all pages, ignoring checksums",
    )
    parser.add_argument(
        "--minify",
        action="store_true",
        help="Minify CSS in the output",
    )
    parser.add_argument(
        "--project",
        type=str,
        help="Cloudflare Pages project name (defaults to settings.toml or 'registrar-monitor')",
    )
    args = parser.parse_args()

    print("⚠️  This script is deprecated. Please use 'monitor deploy' instead.\n")

    service = WebsiteService()

    # Generate
    success = service.generate(
        semester_key=args.semester,
        force=args.force,
        minify=args.minify
    )

    if not success:
        sys.exit(1)

    # Deploy
    if args.deploy:
        project_name = args.project
        if not project_name:
            # Try getting from config
            try:
                config = get_config()
                project_name = config.get("website", {}).get("pages_project_name", "registrar-monitor")
            except Exception:
                project_name = "registrar-monitor"

        service.deploy(project_name=project_name)


if __name__ == "__main__":
    main()
