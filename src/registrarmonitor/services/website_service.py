"""Service for generating and deploying the website."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ..core import get_logger
from ..website.checksums import get_semesters_needing_update, update_checksum
from ..website.config import (
    MILESTONES_MAP,
    OUTPUT_DIR,
    SEMESTER_MAP,
    semester_to_filename,
)
from ..website.data import get_semester_data
from ..website.templates import build_redirect_index, build_semester_page


class WebsiteService:
    """Service for handling website generation and deployment."""

    def __init__(self):
        self.logger = get_logger(__name__)
        # Correct path to assets/website
        # src/registrarmonitor/services/website_service.py -> .../repo/assets/website
        self.website_assets_dir = (
            Path(__file__).parent.parent.parent.parent / "assets" / "website"
        )

    def generate_semester_page(
        self, semester: str, *, minify_assets: bool = False
    ) -> tuple[Optional[Path], float]:
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
        html = build_semester_page(
            data, milestones, semester, minify_assets=minify_assets
        )

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

    def build_frontend_assets(self) -> bool:
        """Build the frontend assets using npm/vite."""
        print("Building frontend assets...")
        build_cmd = ["npm", "run", "build"]
        try:
            # Check if node_modules exists, if not maybe install?
            if not (self.website_assets_dir / "node_modules").exists():
                print("Installing frontend dependencies...")
                subprocess.run(
                    ["npm", "install"], cwd=self.website_assets_dir, check=True
                )

            subprocess.run(build_cmd, cwd=self.website_assets_dir, check=True)
            print("Frontend build successful.")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error building frontend assets: {e}")
            print(f"Error building frontend assets: {e}")
            print("Warning: proceeding without fresh build. Manifest might be outdated.")
            return False
        except FileNotFoundError:
            self.logger.error("npm not found. Is Node.js installed?")
            print("Error: npm not found. Is Node.js installed?")
            return False

    def generate(
        self,
        semester_key: Optional[str] = None,
        force: bool = False,
        minify: bool = False,
    ) -> bool:
        """
        Generate the website.

        Args:
            semester_key: Optional key for specific semester (e.g., 'fall2025')
            force: Force regeneration even if data hasn't changed
            minify: Minify assets

        Returns:
            True if successful
        """
        try:
            # Ensure output directory exists
            OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

            # Build frontend assets first
            self.build_frontend_assets()

            if semester_key:
                # Generate only the specified semester
                if semester_key not in SEMESTER_MAP:
                    print(f"Error: Unknown semester key '{semester_key}'")
                    return False
                semester = SEMESTER_MAP[semester_key]
                print(f"Generating website for {semester}...")
                self.generate_semester_page(semester, minify_assets=minify)
            else:
                # Generate all semesters (incremental by default)
                semesters_to_update = get_semesters_needing_update(force=force)

                if not semesters_to_update:
                    print("All pages up to date.")
                else:
                    print(f"Generating {len(semesters_to_update)} page(s)...")
                    total_size = 0
                    for semester in semesters_to_update:
                        _, size_kb = self.generate_semester_page(
                            semester, minify_assets=minify
                        )
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
            return True

        except Exception as e:
            self.logger.error(f"Website generation failed: {e}")
            print(f"❌ Website generation failed: {e}")
            return False

    def deploy(
        self, project_name: str = "registrar-monitor", branch: Optional[str] = None
    ) -> bool:
        """
        Deploy the website to Cloudflare Pages.

        Args:
            project_name: Cloudflare Pages project name
            branch: Optional branch name for deployment

        Returns:
            True if successful
        """
        print("\n🚀 Deploying to Cloudflare Pages...")
        print(f"   Project: {project_name}")
        if branch:
            print(f"   Branch: {branch}")

        # Command: npx wrangler pages deploy public --project-name <name> [--branch <branch>]
        deploy_cmd = [
            "npx",
            "wrangler",
            "pages",
            "deploy",
            "public",
            "--project-name",
            project_name,
        ]

        if branch:
            deploy_cmd.extend(["--branch", branch])

        try:
            # Run inside website_assets_dir where package.json/node_modules are
            result = subprocess.run(deploy_cmd, cwd=self.website_assets_dir)
            if result.returncode == 0:
                print("✅ Deployment successful!")
                return True
            else:
                print(f"❌ Deployment failed with exit code: {result.returncode}")
                return False
        except FileNotFoundError:
            print("❌ Error: npx/wrangler not found. Is Node.js installed?")
            return False
        except Exception as e:
            self.logger.error(f"Deployment failed: {e}")
            print(f"❌ Deployment failed: {e}")
            return False
