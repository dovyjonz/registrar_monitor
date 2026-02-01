#!/usr/bin/env python3
"""
Generate HTML website for visualizing course enrollment data.

This script queries the database for enrollment data and generates
HTML pages with:
- Course grid layout organized by department
- Expandable sections on course click
- Enrollment history graphs with registration milestones
- Multi-page architecture with semester navigation

Usage:
    # Generate all semester pages (incremental - only changed pages)
    python generate_website.py

    # Force regenerate all pages
    python generate_website.py --force

    # Generate a specific semester only
    python generate_website.py --semester fall2025

    # Generate and deploy to Cloudflare Workers
    python generate_website.py --deploy
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Add scripts directory to path for website module imports
sys.path.insert(0, str(Path(__file__).parent))

from website.checksums import get_semesters_needing_update, update_checksum
from website.config import (
    MILESTONES_MAP,
    OUTPUT_DIR,
    SEMESTER_MAP,
    semester_to_filename,
)
from website.data import get_semester_data
from website.templates import build_redirect_index, build_semester_page


def generate_semester_page(
    semester: str, *, minify_assets: bool = False
) -> tuple[Path | None, float]:
    """
    Generate a single semester page.

    Returns:
        Tuple of (output_path, file_size_kb) - output_path may be None if no data
    """
    print(f"  Generating {semester}...")

    # Get data and milestones
    data = get_semester_data(semester, minify=True)
    milestones = MILESTONES_MAP.get(semester, [])

    # Check if we have data
    if not data.get("cr"):
        print(f"    Warning: No courses found for {semester}")
        return None, 0.0

    # Build HTML
    html = build_semester_page(data, milestones, semester, minify_assets=minify_assets)

    # Write output
    filename = semester_to_filename(semester)
    output_path = OUTPUT_DIR / filename
    output_path.write_text(html)

    # Update checksum
    update_checksum(semester)

    file_size_kb = output_path.stat().st_size / 1024
    course_count = len(data.get("cr", {}))
    snapshot_count = len(data.get("sn", []))
    print(
        f"    {course_count} courses, {snapshot_count} snapshots ({file_size_kb:.1f} KB)"
    )

    return output_path, file_size_kb


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate HTML website for enrollment data visualization"
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy to Cloudflare Workers after generating HTML",
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
    args = parser.parse_args()

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

    # Build frontend assets
    print("Building frontend assets...")
    assets_dir = Path(__file__).parent.parent / "assets" / "website"
    build_cmd = ["npm", "run", "build"]
    try:
        subprocess.run(build_cmd, cwd=assets_dir, check=True)
        print("Frontend build successful.")
    except subprocess.CalledProcessError as e:
        print(f"Error building frontend assets: {e}")
        print("Warning: proceeding without fresh build. Manifest might be outdated.")

    if args.semester:
        # Generate only the specified semester
        semester = SEMESTER_MAP[args.semester]
        print(f"Generating website for {semester}...")
        generate_semester_page(semester, minify_assets=args.minify)
    else:
        # Generate all semesters (incremental by default)
        semesters_to_update = get_semesters_needing_update(force=args.force)

        if not semesters_to_update:
            print("All pages up to date. Use --force to regenerate.")
        else:
            print(f"Generating {len(semesters_to_update)} page(s)...")
            total_size = 0
            for semester in semesters_to_update:
                _, size_kb = generate_semester_page(semester, minify_assets=args.minify)
                total_size += size_kb

            print(
                f"\nGenerated {len(semesters_to_update)} pages ({total_size:.1f} KB total)"
            )

        # Always regenerate index.html (redirect page)
        index_html = build_redirect_index()
        index_path = OUTPUT_DIR / "index.html"
        index_path.write_text(index_html)
        print("Updated index.html (redirect)")

    print(f"\nOutput directory: {OUTPUT_DIR}")

    # Deploy to Cloudflare Workers if --deploy flag is set
    if args.deploy:
        print("\nDeploying to Cloudflare Workers...")
        website_dir = Path(__file__).parent.parent / "assets" / "website"
        deploy_cmd = ["npm", "run", "deploy"]
        result = subprocess.run(deploy_cmd, cwd=website_dir)
        if result.returncode == 0:
            print("Deployment successful!")
        else:
            print(f"Deployment failed with exit code: {result.returncode}")


if __name__ == "__main__":
    main()
