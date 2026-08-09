"""Service for generating and deploying the website."""

import shutil
import subprocess
from pathlib import Path

from ..core import get_logger
from ..data.database_manager import DatabaseManager
from ..website.checksums import get_semesters_needing_update, update_checksum
from ..website.config import (
    MILESTONES_MAP,
    OUTPUT_DIR,
    SEMESTER_MAP,
    _get_indexing,
    course_to_slug,
    semester_to_filename,
    semester_to_slug,
)
from ..website.data import get_prototype_payloads, get_semester_data
from ..website.static_manifest import build_frontend_payloads_v3, publish_semester
from ..website.templates import (
    build_prototype_page,
    build_redirect_index,
    build_semester_page,
)

DEPLOY_TIMEOUT_SECONDS = 900


class WebsiteService:
    """Service for handling website generation and deployment."""

    def __init__(
        self,
        output_dir: Path | None = None,
    ):
        self.logger = get_logger(__name__)
        self.last_generation_skipped = False
        self._output_dir = output_dir.resolve() if output_dir is not None else None
        # Correct path to assets/website
        # src/registrarmonitor/services/website_service.py -> .../repo/assets/website
        self._default_website_assets_dir = (
            Path(__file__).parent.parent.parent.parent / "assets" / "website"
        )
        self.website_assets_dir = self._default_website_assets_dir
        self._semester_data_cache: dict[str, dict] = {}

    def _get_semester_data(self, semester: str) -> dict:
        """Load each semester payload at most once during a generation run."""
        if semester not in self._semester_data_cache:
            self._semester_data_cache[semester] = get_semester_data(
                semester, minify=True
            )
        return self._semester_data_cache[semester]

    @property
    def output_dir(self) -> Path:
        """Return an explicit isolated root or the current configured root."""
        if self._output_dir is not None:
            return self._output_dir
        if self.website_assets_dir != self._default_website_assets_dir:
            return self.website_assets_dir / "public"
        return OUTPUT_DIR

    @property
    def checksums_file(self) -> Path:
        return self.output_dir / ".checksums.json"

    def generate_semester_page(
        self,
        semester: str,
        *,
        minify_assets: bool = False,
        database: DatabaseManager | None = None,
    ) -> tuple[Path | None, float]:
        """
        Generate a single semester page.

        Returns:
            Tuple of (output_path, file_size_kb) - output_path may be None if no data
        """
        print(f"  Generating {semester}...", flush=True)

        # Get data and milestones
        data = (
            get_semester_data(semester, minify=True, database=database)
            if database is not None
            else self._get_semester_data(semester)
        )
        milestones = MILESTONES_MAP.get(semester, [])

        # Check if we have data
        if not data.get("cr"):
            print(
                f"    Warning: No courses found for {semester}; generating empty page"
            )

        # Build HTML
        html = build_semester_page(
            data,
            milestones,
            semester,
            minify_assets=minify_assets,
            manifest_path=self.output_dir / "assets" / ".vite" / "manifest.json",
        )

        # Write output HTML
        filename = semester_to_filename(semester)
        output_path = self.output_dir / filename
        output_path.write_text(html)

        # Publish the v3 read model consumed by the generated frontend. The
        # stable pointer is the only JSON URL embedded in the HTML above.
        summary_payload, departments = build_frontend_payloads_v3(
            data=data,
            milestones=milestones,
            semester=semester,
        )
        publish_semester(
            self.output_dir,
            semester_slug=semester_to_slug(semester),
            semester=semester,
            current_snapshot=summary_payload["currentSnapshot"],
            summary=summary_payload,
            departments=departments,
        )

        # Remove a stale pre-v3 root payload when regenerating an existing
        # public directory. The v3 manifest pointer is the only data entrypoint.
        root_payload_path = self.output_dir / filename.replace(".html", ".json")
        root_payload_path.unlink(missing_ok=True)

        # Update checksum
        update_checksum(semester, self.checksums_file, database=database)

        file_size_kb = output_path.stat().st_size / 1024
        course_count = len(data.get("cr", {}))
        snapshot_count = len(data.get("sn", []))
        print(
            f"    {course_count} courses, {snapshot_count} snapshots ({file_size_kb:.1f} KB)"
        )

        return output_path, file_size_kb

    def generate_prototype(self, semester_key: str | None = None) -> bool:
        """Generate the local-only dashboard redesign prototype."""
        if self._output_dir is not None:
            print(
                "❌ Prototype generation does not support an isolated output directory."
            )
            return False
        try:
            target_semester = (
                SEMESTER_MAP[semester_key]
                if semester_key
                else SEMESTER_MAP["summer2026"]
            )
        except KeyError:
            print(f"Error: Unknown semester key '{semester_key}'")
            return False

        try:
            OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

            if not self.build_frontend_assets():
                print("❌ Frontend build failed. Aborting prototype generation.")
                return False

            import json

            prototype_data_dir = OUTPUT_DIR / "prototype-data"
            if prototype_data_dir.exists():
                shutil.rmtree(prototype_data_dir)
            prototype_data_dir.mkdir(parents=True, exist_ok=True)

            candidate_semesters = (
                [target_semester]
                if semester_key
                else [
                    target_semester,
                    *[
                        semester
                        for semester in SEMESTER_MAP.values()
                        if semester != target_semester
                    ],
                ]
            )
            generated_payloads: list[
                tuple[str, str, dict[str, object], dict[str, dict[str, object]]]
            ] = []

            print(f"Generating local dashboard prototype for {target_semester}...")
            for semester in candidate_semesters:
                sem_slug = semester_to_slug(semester)
                detail_base_url = f"prototype-data/{sem_slug}"
                index_payload, detail_payloads = get_prototype_payloads(
                    semester,
                    detail_base_url=detail_base_url,
                )
                if not index_payload.get("courseRows"):
                    print(f"  Skipping {semester}: no courses found")
                    continue
                generated_payloads.append(
                    (semester, sem_slug, index_payload, detail_payloads)
                )

            if not generated_payloads:
                print(f"Warning: No courses found for {target_semester}")
                return False

            semester_options = [
                {
                    "semester": semester,
                    "indexUrl": f"prototype-data/{sem_slug}/index.json",
                }
                for semester, sem_slug, _, _ in generated_payloads
            ]

            active_index_payload: dict[str, object] | None = None
            total_detail_files = 0
            for (
                semester,
                sem_slug,
                index_payload,
                detail_payloads,
            ) in generated_payloads:
                index_payload["semesters"] = semester_options
                semester_dir = prototype_data_dir / sem_slug
                semester_dir.mkdir(parents=True, exist_ok=True)

                index_path = semester_dir / "index.json"
                index_path.write_text(
                    json.dumps(index_payload, separators=(",", ":")),
                    encoding="utf-8",
                )

                for slug, payload in detail_payloads.items():
                    detail_path = semester_dir / f"{slug}.json"
                    detail_path.write_text(
                        json.dumps(payload, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    total_detail_files += 1

                if semester == target_semester:
                    active_index_payload = index_payload

            if active_index_payload is None:
                active_index_payload = generated_payloads[0][2]

            root_index_path = prototype_data_dir / "index.json"
            root_index_path.write_text(
                json.dumps(active_index_payload, separators=(",", ":")),
                encoding="utf-8",
            )

            html = build_prototype_page(
                semester=target_semester,
                index_json="prototype-data/index.json",
            )
            output_path = OUTPUT_DIR / "prototype.html"
            output_path.write_text(html, encoding="utf-8")

            active_course_rows = active_index_payload.get("courseRows", [])
            active_course_count = (
                len(active_course_rows) if isinstance(active_course_rows, list) else 0
            )
            print(
                f"Prototype written to {output_path} "
                f"({active_course_count} active courses, "
                f"{total_detail_files} detail files)"
            )
            print(
                "Serve assets/website/public locally and open "
                "http://127.0.0.1:8000/prototype.html"
            )
            return True
        except Exception as e:
            self.logger.error(f"Prototype generation failed: {e}")
            print(f"❌ Prototype generation failed: {e}")
            return False

    def _patch_asset_hashes_in_html(self) -> bool:
        """Update JS/CSS asset references in all deployed HTML files after a Vite build.

        Vite produces content-hashed filenames (e.g. ``main-BTBUBuTQ.js``) that
        change on every rebuild.  Rather than regenerating every HTML page from
        scratch (which requires DB queries for each semester), this method reads
        the new manifest and performs a targeted in-place regex substitution on
        the already-deployed HTML files — updating the script, module-preload,
        and stylesheet references to versioned assets. Checksums are left intact
        so incremental logic still skips semesters with no new data.

        Returns:
            True if the patch was applied successfully, False otherwise.
        """
        import json
        import re

        manifest_path = self.output_dir / "assets" / ".vite" / "manifest.json"
        if not manifest_path.exists():
            print(
                "Warning: manifest.json not found — cannot patch asset hashes in HTML."
            )
            return False

        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as e:
            print(f"Warning: Failed to read manifest.json: {e}")
            return False

        entry = manifest.get("src/main.js", {})
        new_js = entry.get("file")  # e.g. "main-BTBUBuTQ.js"
        css_files = entry.get("css", [])
        new_css = css_files[0] if css_files else None  # e.g. "main-CKing-67.css"

        if not new_js:
            print("Warning: No JS entry in manifest — skipping asset hash patch.")
            return False

        output_dir = self.output_dir
        patched = 0
        for html_file in output_dir.glob("*.html"):
            text = html_file.read_text(encoding="utf-8")
            original = text

            # Replace executable and module-preload JS references.
            text = re.sub(
                r'((?:src|href)="assets/)main-[^"]+\.js(")',
                rf"\g<1>{new_js}\2",
                text,
            )

            # Replace any existing hashed CSS reference (assets/main-*.css)
            if new_css:
                text = re.sub(
                    r'(href="assets/)main-[^"]+\.css(")',
                    rf"\g<1>{new_css}\2",
                    text,
                )

            if text != original:
                html_file.write_text(text, encoding="utf-8")
                patched += 1

        print(
            f"Patched asset hashes in {patched} HTML file(s) — no page regeneration needed."
        )
        return True

    def _frontend_dependencies_need_install(self) -> bool:
        """Return True when frontend dependencies are missing or stale."""
        import json

        node_modules = self.website_assets_dir / "node_modules"
        if not node_modules.exists():
            return True

        package_json = self.website_assets_dir / "package.json"
        try:
            package_data = json.loads(package_json.read_text())
        except (OSError, json.JSONDecodeError):
            return True

        frontend_dependencies = {
            *package_data.get("dependencies", {}).keys(),
            *package_data.get("devDependencies", {}).keys(),
        }
        if any(
            not (node_modules / dependency).exists()
            for dependency in frontend_dependencies
        ):
            return True

        installed_lock = node_modules / ".package-lock.json"
        if not installed_lock.exists():
            return True

        installed_mtime = installed_lock.stat().st_mtime
        dependency_sources = [
            self.website_assets_dir / "package-lock.json",
            package_json,
        ]
        return any(
            source.exists() and source.stat().st_mtime > installed_mtime
            for source in dependency_sources
        )

    def _validate_asset_references_in_html(self) -> bool:
        """Return whether generated HTML references existing built assets."""
        import re

        output_dir = self.output_dir
        valid = True
        for html_file in output_dir.glob("*.html"):
            text = html_file.read_text(encoding="utf-8")
            asset_urls = re.findall(
                r'(?:src|href)="(assets/main-[^"]+\.(?:js|css))"',
                text,
            )
            for asset_url in asset_urls:
                if not (output_dir / asset_url).is_file():
                    message = (
                        f"Missing frontend asset referenced by "
                        f"{html_file.name}: {asset_url}"
                    )
                    self.logger.error(message)
                    print(f"Error: {message}")
                    valid = False
        return valid

    def build_frontend_assets(self) -> bool:
        """Build the frontend assets using npm/vite.

        After a successful build, Vite emits new content-hashed filenames.
        Rather than regenerating all HTML pages (which requires DB queries),
        we patch the asset references in-place using :meth:`_patch_asset_hashes_in_html`.
        Checksums are preserved so incremental logic is unaffected.
        """
        import os

        print("Building frontend assets...")
        build_cmd = ["npm", "run", "build"]

        # Constrain Node.js memory and disable telemetry to prevent OOM on 1GB VMs
        env = os.environ.copy()
        env["NODE_OPTIONS"] = "--max_old_space_size=512"
        env["CLOUDFLARE_TELEMETRY_DISABLED"] = "1"
        env["NO_UPDATE_NOTIFIER"] = "1"
        env["REGISTRAR_VITE_OUTPUT_DIR"] = str(self.output_dir / "assets")
        if self._output_dir is not None:
            env["REGISTRAR_MAIN_ONLY"] = "1"

        try:
            if self._frontend_dependencies_need_install():
                print("Installing/updating frontend dependencies...")
                subprocess.run(
                    ["npm", "install"], cwd=self.website_assets_dir, check=True, env=env
                )

            subprocess.run(build_cmd, cwd=self.website_assets_dir, check=True, env=env)
            print("Frontend build successful.")

            # Patch asset hashes in all deployed HTML files without regenerating them.
            if not self._patch_asset_hashes_in_html():
                return False
            return self._validate_asset_references_in_html()
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Error building frontend assets: {e}")
            print(f"Error building frontend assets: {e}")
            print(
                "Warning: proceeding without fresh build. Manifest might be outdated."
            )
            return False
        except FileNotFoundError:
            self.logger.error("npm not found. Is Node.js installed?")
            print("Error: npm not found. Is Node.js installed?")
            return False

    def _generate_course_share_pages(self, semesters: list[str]) -> None:
        """Regenerate share pages only for semesters whose data changed."""
        from ..website.templates import build_course_share_page

        share_dir = self.output_dir / "courses"
        share_dir.mkdir(parents=True, exist_ok=True)

        generated = 0

        for semester in semesters:
            sem_slug = semester_to_slug(semester)
            sem_dir = share_dir / sem_slug
            if sem_dir.exists():
                shutil.rmtree(sem_dir)
            sem_dir.mkdir(parents=True, exist_ok=True)

            print(f"  Generating {semester} course share pages...", flush=True)
            data = self._get_semester_data(semester)
            courses = data.get("cr", {})
            if not courses:
                continue

            for code, course in courses.items():
                title = course.get("ti", "")
                fill = course.get("af", 0)
                section_count = len(course.get("s", {}))

                html = build_course_share_page(
                    semester=semester,
                    course_code=code,
                    course_title=title,
                    course_fill=fill,
                    section_count=section_count,
                )

                course_slug = course_to_slug(code)
                out_path = sem_dir / f"{course_slug}.html"
                out_path.write_text(html)
                generated += 1

        print(f"Generated {generated} course share pages", flush=True)

    def _build_headers_content(self) -> str:
        """Build Cloudflare Pages headers for generated public output."""
        indexing = _get_indexing().strip()
        robots_header = f"  X-Robots-Tag: {indexing}\n" if indexing else ""
        return f"""/*
  Cache-Control: public, max-age=0, must-revalidate
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'; upgrade-insecure-requests
{robots_header}
/assets/*
  Cache-Control: public, max-age=31536000, immutable

/data/*/manifest.json
  Cache-Control: no-cache

/data/*/manifests/*
  Cache-Control: public, max-age=31536000, immutable

/data/blobs/*
  Cache-Control: public, max-age=31536000, immutable

/courses/*
  Cache-Control: public, max-age=300, stale-while-revalidate=600
"""

    def _build_robots_content(self) -> str:
        """Build robots.txt from the configured indexing directive."""
        indexing = _get_indexing().strip().lower()
        blocks_indexing = "noindex" in indexing or "none" in indexing
        directive = "Disallow: /" if blocks_indexing else "Allow: /"
        return f"User-agent: *\n{directive}\n"

    def validate_public_output(self) -> list[str]:
        """Validate that the public output directory contains only allowed files.

        Returns a list of error messages for disallowed artifacts.
        Allowed: HTML, JSON under data/, assets/, _headers, robots.txt,
        courses/, and .checksums.json.
        """
        errors = []
        allowed_extensions = {".html", ".json"}
        allowed_names = {"_headers", "robots.txt", ".checksums.json"}
        allowed_dirs = {"assets", "courses", "data"}
        private_names = {".DS_Store", ".env"}
        private_suffixes = {".db", ".key", ".log", ".pem", ".sqlite", ".sqlite3"}

        if not self.output_dir.is_dir():
            return [f"Output directory does not exist: {self.output_dir}"]

        for item in self.output_dir.rglob("*"):
            if not item.is_file():
                continue
            name = item.name
            if (
                name in private_names
                or name.startswith(".env.")
                or item.suffix.lower() in private_suffixes
            ):
                errors.append(f"Private artifact: {item.relative_to(self.output_dir)}")

        for item in self.output_dir.iterdir():
            name = item.name
            if item.is_dir():
                if name not in allowed_dirs:
                    errors.append(f"Unexpected directory: {name}/")
            elif item.is_file():
                if name in allowed_names:
                    continue
                ext = item.suffix
                if ext == ".json":
                    errors.append(f"Unexpected root JSON payload: {name}")
                    continue
                if ext in allowed_extensions:
                    continue
                if name.startswith("."):
                    # Hidden files like .DS_Store
                    errors.append(f"Hidden file: {name}")
                else:
                    errors.append(f"Unexpected file: {name}")

        return errors

    def is_any_semester_active(self, buffer_days: int = 7) -> bool:
        """Check if we are currently within an active registration window."""
        import datetime

        from ..config import get_timezone
        from ..website.config import ALL_SEMESTERS, get_milestones

        registrar_timezone = get_timezone()
        now = datetime.datetime.now(registrar_timezone)

        for semester in ALL_SEMESTERS:
            try:
                milestones = get_milestones(semester)
                if not milestones:
                    continue

                times = []
                for milestone in milestones:
                    value = datetime.datetime.fromisoformat(milestone["time"])
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=registrar_timezone)
                    else:
                        value = value.astimezone(registrar_timezone)
                    times.append(value)
                earliest = min(times) - datetime.timedelta(days=buffer_days)
                latest = max(times) + datetime.timedelta(days=buffer_days)

                if earliest <= now <= latest:
                    return True
            except Exception:
                continue
        return False

    def generate(
        self,
        semester_key: str | None = None,
        force: bool = False,
        minify: bool = True,
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
            self.last_generation_skipped = False
            self._semester_data_cache.clear()
            # Skip if not active (unless forced)
            if not force and not self.is_any_semester_active():
                print("💤 Outside active registration windows. Skipping build/deploy.")
                print("   (Use --force to override)")
                self.last_generation_skipped = True
                return True

            # Ensure output directory exists
            self.output_dir.mkdir(exist_ok=True, parents=True)

            # Build frontend assets first
            if not self.build_frontend_assets():
                print("❌ Frontend build failed. Aborting website generation.")
                return False

            if semester_key:
                # Generate only the specified semester
                if semester_key not in SEMESTER_MAP:
                    print(f"Error: Unknown semester key '{semester_key}'")
                    return False
                semester = SEMESTER_MAP[semester_key]
                print(f"Generating website for {semester}...")
                self.generate_semester_page(semester, minify_assets=minify)
                self._generate_course_share_pages([semester])
            else:
                # Generate all semesters (incremental by default)
                semesters_to_update = get_semesters_needing_update(
                    force=force, checksums_file=self.checksums_file
                )

                if not semesters_to_update:
                    print("All pages up to date.")
                else:
                    print(f"Generating {len(semesters_to_update)} page(s)...")
                    total_size = 0.0
                    for semester in semesters_to_update:
                        _, size_kb = self.generate_semester_page(
                            semester, minify_assets=minify
                        )
                        total_size += size_kb

                    print(
                        f"\nGenerated {len(semesters_to_update)} pages ({total_size:.1f} KB total)"
                    )

                # Reuse the already-loaded payloads and touch only changed semesters.
                if semesters_to_update:
                    self._generate_course_share_pages(semesters_to_update)

                # Always regenerate index.html (redirect page)
                index_html = build_redirect_index()
                index_path = self.output_dir / "index.html"
                index_path.write_text(index_html)
                print("Updated index.html (redirect)")

                # Generate Cloudflare _headers file with security headers
                headers_path = self.output_dir / "_headers"
                headers_path.write_text(self._build_headers_content())
                print("Generated Cloudflare _headers")

                # Generate robots.txt
                robots_path = self.output_dir / "robots.txt"
                robots_path.write_text(self._build_robots_content())
                print("Generated robots.txt")

            # Validate public output before any deploy can publish private artifacts.
            issues = self.validate_public_output()
            if issues:
                print("Public output validation errors:")
                for issue in issues:
                    print(f"   - {issue}")
                return False

            print(f"\nOutput directory: {self.output_dir}")
            return True

        except Exception as e:
            self.logger.error(f"Website generation failed: {e}")
            print(f"❌ Website generation failed: {e}")
            return False

    def deploy(
        self, project_name: str = "registrar-monitor", branch: str | None = None
    ) -> bool:
        """
        Deploy the website to Cloudflare Pages.

        Args:
            project_name: Cloudflare Pages project name
            branch: Optional branch name for deployment

        Returns:
            True if successful
        """
        import os

        if self._output_dir is not None:
            print("❌ Deployment is disabled for isolated generation output.")
            return False

        print("\n🚀 Deploying to Cloudflare Pages...", flush=True)
        print(f"   Project: {project_name}")
        if branch:
            print(f"   Branch: {branch}")

        issues = self.validate_public_output()
        if issues:
            print("❌ Cloudflare deploy skipped: public output validation failed.")
            for issue in issues:
                print(f"   - {issue}")
            return False

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

        # Constrain Node.js memory and disable telemetry to prevent OOM on 1GB VMs
        env = os.environ.copy()
        if not env.get("CLOUDFLARE_API_TOKEN", "").strip():
            print("❌ Cloudflare deploy skipped: CLOUDFLARE_API_TOKEN is not set.")
            print(
                "   Add CLOUDFLARE_API_TOKEN=... to /home/dmitry_s_ivanenko/registrar_monitor/.env"
            )
            print("   Then restart the systemd service so EnvironmentFile is reloaded.")
            return False
        if not env.get("CLOUDFLARE_ACCOUNT_ID", "").strip():
            print("❌ Cloudflare deploy skipped: CLOUDFLARE_ACCOUNT_ID is not set.")
            print(
                "   Add CLOUDFLARE_ACCOUNT_ID=... to /home/dmitry_s_ivanenko/registrar_monitor/.env"
            )
            print(
                "   Wrangler custom API tokens can fail account discovery without it."
            )
            print("   Then restart the systemd service so EnvironmentFile is reloaded.")
            return False

        env["NODE_OPTIONS"] = "--max_old_space_size=512"
        env["CLOUDFLARE_TELEMETRY_DISABLED"] = "1"
        env["NO_UPDATE_NOTIFIER"] = "1"

        try:
            # Inherit stdout/stderr so Wrangler progress reaches the operator and
            # systemd journal immediately. Bound the process so a stalled network
            # upload cannot block the scheduler indefinitely.
            result = subprocess.run(
                deploy_cmd,
                cwd=self.website_assets_dir,
                env=env,
                timeout=DEPLOY_TIMEOUT_SECONDS,
            )
            if result.returncode == 0:
                print("✅ Deployment successful!")
                return True
            else:
                print(f"❌ Deployment failed with exit code: {result.returncode}")
                return False
        except FileNotFoundError:
            print("❌ Error: npx/wrangler not found. Is Node.js installed?")
            return False
        except subprocess.TimeoutExpired:
            print(
                f"❌ Deployment timed out after {DEPLOY_TIMEOUT_SECONDS // 60} minutes."
            )
            return False
        except Exception as e:
            self.logger.error(f"Deployment failed: {e}")
            print(f"❌ Deployment failed: {e}")
            return False
