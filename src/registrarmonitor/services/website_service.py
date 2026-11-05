"""Service for generating the website and deploying it as Worker static assets."""

import hashlib
import json
import re
import resource
import shutil
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..core import get_logger
from ..data.database_manager import DatabaseManager
from ..website.catalog import build_publication_catalog
from ..website.checksums import (
    compute_semester_hash,
    get_semesters_needing_update,
    update_checksum,
)
from ..website.config import (
    OUTPUT_DIR,
    content_security_policy,
    get_configured_semesters,
    get_milestones,
    semester_key_map,
    semester_to_slug,
)
from ..website.data import get_semester_data
from ..website.preview import (
    build_course_preview_state,
    build_semester_preview_state,
    publish_preview_state,
)
from ..website.static_manifest import (
    build_frontend_payloads_v3,
    publish_semester,
    write_if_changed,
)
from ..website.templates import (
    PageShell,
    build_page_shell,
    build_redirect_index,
    build_semester_page,
)

DEPLOY_TIMEOUT_SECONDS = 900
WORKER_CONFIG = Path(__file__).resolve().parents[3] / "workers/site/wrangler.jsonc"

_ASSET_REFERENCE = re.compile(r'(?:src|href)="(/assets/[^"]+\.(?:js|css))"')
_PREVIEW_HASH_REFERENCE = re.compile(r"(?:/|&quot;)([A-Za-z0-9_-]{8})(?:\.json|\.png)")


def peak_rss_mb() -> float:
    """Return this process's peak resident set size in megabytes."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is KB on Linux and bytes on macOS.
    return peak / (1024 * 1024 if sys.platform == "darwin" else 1024)


@dataclass(frozen=True)
class _HtmlScan:
    """What one generated page references; enough to validate and prune."""

    asset_urls: tuple[str, ...]
    preview_hashes: frozenset[str]


class WebsiteService:
    """Service for handling website generation and deployment."""

    def __init__(
        self,
        output_dir: Path | None = None,
    ):
        self.logger = get_logger(__name__)
        self.last_generation_skipped = False
        self._output_dir = output_dir.resolve() if output_dir is not None else None
        # src/registrarmonitor/services/website_service.py -> .../repo/frontend
        self._default_website_assets_dir = (
            Path(__file__).parent.parent.parent.parent / "frontend"
        )
        self.website_assets_dir = self._default_website_assets_dir
        self._semester_data_cache: dict[tuple[str, bool], dict] = {}
        self._html_scan_cache: dict[Path, tuple[tuple[int, int], _HtmlScan]] = {}

    @contextmanager
    def _phase(self, name: str) -> Generator[None]:
        """Report a phase's start, duration, and peak RSS so long runs are not silent."""
        print(f"  [{name}] started", flush=True)
        started = time.perf_counter()
        try:
            yield
        finally:
            print(
                f"  [{name}] finished in {time.perf_counter() - started:.1f}s "
                f"(peak RSS {peak_rss_mb():.0f} MB)",
                flush=True,
            )

    def _write_page(self, path: Path, html: str) -> bool:
        """Write a generated page unless it is unchanged; return whether it changed."""
        self._html_scan_cache.pop(path, None)
        return write_if_changed(path, html)

    def _scan_html(self, page: Path) -> _HtmlScan:
        """Read a generated page once and reuse the result while it is unchanged."""
        stat = page.stat()
        key = (stat.st_size, stat.st_mtime_ns)
        cached = self._html_scan_cache.get(page)
        if cached is not None and cached[0] == key:
            return cached[1]
        text = page.read_text(encoding="utf-8")
        scan = _HtmlScan(
            asset_urls=tuple(_ASSET_REFERENCE.findall(text)),
            preview_hashes=frozenset(_PREVIEW_HASH_REFERENCE.findall(text)),
        )
        self._html_scan_cache[page] = (key, scan)
        return scan

    def _get_semester_data(self, semester: str, *, archive_window: bool) -> dict:
        """Load each semester payload at most once during a generation run."""
        key = (semester, archive_window)
        if key not in self._semester_data_cache:
            self._semester_data_cache[key] = get_semester_data(
                semester,
                minify=True,
                archive_window=archive_window,
            )
        return self._semester_data_cache[key]

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
        publication_semesters: list[str] | None = None,
    ) -> tuple[Path | None, float]:
        """
        Generate a single semester page.

        Returns:
            Tuple of (output_path, file_size_kb) - output_path may be None if no data
        """
        print(f"  Generating {semester}...", flush=True)

        semesters = publication_semesters or [
            entry.label for entry in build_publication_catalog()
        ]
        archived = bool(semesters and semester != semesters[0])
        milestones = get_milestones(semester)

        # Fingerprint the stored data before reading it. A snapshot stored while
        # the build runs then differs from this checksum, so the next run
        # regenerates instead of recording the stale pages as published.
        checksum = compute_semester_hash(
            semester, database=database, milestones=milestones, archived=archived
        )

        with self._phase(f"{semester}: read data"):
            data = (
                get_semester_data(
                    semester,
                    minify=True,
                    database=database,
                    archive_window=archived,
                )
                if database is not None
                else self._get_semester_data(semester, archive_window=archived)
            )

        # Check if we have data
        if not data.get("cr"):
            print(
                f"    Warning: No courses found for {semester}; generating empty page"
            )

        # Publish the v3 read model consumed by the generated frontend. The
        # stable pointer is the only mutable JSON URL embedded in the HTML.
        with self._phase(f"{semester}: publish data"):
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

        semester_preview = build_semester_preview_state(
            summary=summary_payload,
            departments=departments,
            milestones=milestones,
            archived=archived,
        )
        publish_preview_state(self.output_dir, semester_preview)

        # The Vite manifest and navigation are shared by every page of the semester.
        shell = build_page_shell(
            semester,
            manifest_path=self.output_dir / "assets" / ".vite" / "manifest.json",
            semesters=semesters,
        )
        html = build_semester_page(
            data,
            milestones,
            semester,
            minify_assets=minify_assets,
            manifest_path=self.output_dir / "assets" / ".vite" / "manifest.json",
            semesters=semesters,
            preview_state=semester_preview,
            shell=shell,
        )
        output_path = (
            self.output_dir / "semesters" / semester_to_slug(semester) / "index.html"
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_page(output_path, html)
        with self._phase(f"{semester}: course routes"):
            course_count = self._publish_course_routes(
                semester=semester,
                data=data,
                milestones=milestones,
                departments=departments,
                semesters=semesters,
                archived_semester=archived,
                published_at=(summary_payload.get("currentSnapshot") or {}).get(
                    "observedAt"
                ),
                minify_assets=minify_assets,
                shell=shell,
            )

        update_checksum(semester, checksum, self.checksums_file)

        file_size_kb = output_path.stat().st_size / 1024
        snapshot_count = len(data.get("sn", []))
        print(
            f"    {course_count} courses, {snapshot_count} snapshots ({file_size_kb:.1f} KB)"
        )

        return output_path, file_size_kb

    def _published_course_state(self, page: Path) -> dict | None:
        """Read the exact preview state referenced by an existing course route."""
        if not page.is_file():
            return None
        match = re.search(
            r'data-preview-state-url="([^"]+)"',
            page.read_text(encoding="utf-8"),
        )
        if match is None:
            return None
        state_path = self.output_dir / match.group(1).lstrip("/")
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _write_course_route(
        self,
        *,
        state: dict,
        data: dict,
        milestones: list[dict[str, str]],
        semesters: list[str],
        minify_assets: bool,
        shell: PageShell | None = None,
    ) -> Path:
        route = (
            self.output_dir
            / "courses"
            / state["semesterSlug"]
            / state["slug"]
            / "index.html"
        )
        route.parent.mkdir(parents=True, exist_ok=True)
        html = build_semester_page(
            data,
            milestones,
            state["semester"],
            minify_assets=minify_assets,
            manifest_path=self.output_dir / "assets" / ".vite" / "manifest.json",
            semesters=semesters,
            course_state=state,
            shell=shell,
        )
        self._write_page(route, html)
        return route

    def _publish_course_routes(
        self,
        *,
        semester: str,
        data: dict,
        milestones: list[dict[str, str]],
        departments: dict[str, dict],
        semesters: list[str],
        archived_semester: bool,
        published_at: str | None,
        minify_assets: bool,
        shell: PageShell | None = None,
    ) -> int:
        """Publish current routes and archive previously published removals."""
        states: dict[str, dict] = {}
        for department in departments.values():
            timestamps = department["timestamps"]
            for course in department["courses"].values():
                state = build_course_preview_state(
                    semester=semester,
                    course=course,
                    timestamps=timestamps,
                    milestones=milestones,
                    published_at=published_at,
                    archived=archived_semester,
                )
                states[state["slug"]] = state

        semester_course_root = self.output_dir / "courses" / semester_to_slug(semester)
        for page in semester_course_root.glob("*/index.html"):
            if page.parent.name in states:
                continue
            previous = self._published_course_state(page)
            if (
                previous is None
                or previous.get("kind") != "course"
                or previous.get("semester") != semester
            ):
                continue
            if previous.get("archived"):
                continue
            states[page.parent.name] = build_course_preview_state(
                semester=semester,
                course=previous["course"],
                timestamps=previous["timestamps"],
                milestones=milestones,
                published_at=published_at,
                removed=True,
            )

        if shell is None:
            shell = build_page_shell(
                semester,
                manifest_path=self.output_dir / "assets" / ".vite" / "manifest.json",
                semesters=semesters,
            )
        # Previews are content-addressed: an unchanged course hashes to the file
        # already on disk, which is verified rather than rewritten, and its page
        # renders to the same bytes, which are not rewritten either.
        for state in states.values():
            publish_preview_state(self.output_dir, state)
            self._write_course_route(
                state=state,
                data=data,
                milestones=milestones,
                semesters=semesters,
                minify_assets=minify_assets,
                shell=shell,
            )
        print(f"  Published {len(states)} clean course routes", flush=True)
        return len(states)

    def _patch_asset_hashes_in_html(self) -> bool:
        """Update JS/CSS asset references in all deployed HTML files after a Vite build.

        Vite produces content-hashed filenames (e.g. ``main-BTBUBuTQ.js``) that
        change on every rebuild.  Rather than regenerating every HTML page from
        scratch (which requires DB queries for each semester), this method reads
        the new manifest and performs a targeted in-place regex substitution on
        the already-deployed HTML files, updating the script, module-preload,
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
                "Warning: manifest.json not found - cannot patch asset hashes in HTML."
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
            print("Warning: No JS entry in manifest - skipping asset hash patch.")
            return False

        output_dir = self.output_dir
        patched = 0
        for html_file in output_dir.rglob("*.html"):
            text = html_file.read_text(encoding="utf-8")
            original = text

            # Replace executable and module-preload JS references.
            text = re.sub(
                r'((?:src|href)="/assets/)main-[^"]+\.js(")',
                rf"\g<1>{new_js}\2",
                text,
            )

            # Replace any existing hashed CSS reference (assets/main-*.css)
            if new_css:
                text = re.sub(
                    r'(href="/assets/)main-[^"]+\.css(")',
                    rf"\g<1>{new_css}\2",
                    text,
                )

            if text != original:
                self._write_page(html_file, text)
                patched += 1

        print(
            f"Patched asset hashes in {patched} HTML file(s) - no page regeneration needed."
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

    def _frontend_asset_reference_issues(self) -> list[str]:
        """Return missing local frontend assets referenced by generated HTML."""
        output_dir = self.output_dir
        issues: list[str] = []
        exists: dict[str, bool] = {}
        for html_file in output_dir.rglob("*.html"):
            for asset_url in self._scan_html(html_file).asset_urls:
                if asset_url not in exists:
                    exists[asset_url] = (output_dir / asset_url.lstrip("/")).is_file()
                if not exists[asset_url]:
                    issues.append(
                        "Missing frontend asset referenced by "
                        f"{html_file.relative_to(output_dir)}: {asset_url}"
                    )
        return issues

    def _validate_asset_references_in_html(self) -> bool:
        """Return whether generated HTML references existing built assets."""
        issues = self._frontend_asset_reference_issues()
        for issue in issues:
            self.logger.error(issue)
            print(f"Error: {issue}")
        return not issues

    def _frontend_source_hash(self) -> str:
        """Hash everything the Vite bundle depends on (source, config, lockfile)."""
        digest = hashlib.sha256()
        root = self.website_assets_dir
        sources = [
            root / "package.json",
            root / "package-lock.json",
            root / "vite.config.js",
            *sorted(path for path in (root / "src").rglob("*") if path.is_file()),
        ]
        for path in sources:
            if path.is_file():
                digest.update(path.relative_to(root).as_posix().encode())
                digest.update(path.read_bytes())
        return digest.hexdigest()

    @property
    def _build_hash_file(self) -> Path:
        return self.output_dir / ".frontend-build-hash"

    def build_frontend_assets(self) -> bool:
        """Build the frontend assets using npm/vite, unless they are current.

        The 1 GB VM rebuilds only when the frontend source, config, or lockfile
        hash differs from the one recorded by the last successful build; routine
        publications reuse the existing bundle and only validate its references.

        After a successful build, Vite emits new content-hashed filenames.
        Rather than regenerating all HTML pages (which requires DB queries),
        we patch the asset references in-place using :meth:`_patch_asset_hashes_in_html`.
        Checksums are preserved so incremental logic is unaffected.
        """
        import os

        source_hash = self._frontend_source_hash()
        manifest = self.output_dir / "assets" / ".vite" / "manifest.json"
        try:
            recorded = self._build_hash_file.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if recorded == source_hash and manifest.is_file():
            print("Frontend assets are current; skipping build.")
            return self._validate_asset_references_in_html()

        print("Building frontend assets...")
        build_cmd = ["npm", "run", "build"]

        # Constrain Node.js memory and disable telemetry to prevent OOM on 1GB VMs
        env = os.environ.copy()
        env["NODE_OPTIONS"] = "--max_old_space_size=512"
        env["CLOUDFLARE_TELEMETRY_DISABLED"] = "1"
        env["NO_UPDATE_NOTIFIER"] = "1"
        env["REGISTRAR_VITE_OUTPUT_DIR"] = str(self.output_dir / "assets")

        try:
            if self._frontend_dependencies_need_install():
                message = (
                    "Frontend dependencies are missing or out of date. "
                    "Run `npm ci --prefix frontend` as the operator, then retry."
                )
                self.logger.error(message)
                print(f"Error: {message}")
                return False

            subprocess.run(build_cmd, cwd=self.website_assets_dir, check=True, env=env)
            print("Frontend build successful.")

            # Patch asset hashes in all deployed HTML files without regenerating them.
            if not self._patch_asset_hashes_in_html():
                return False
            if not self._validate_asset_references_in_html():
                return False
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._build_hash_file.write_text(source_hash + "\n", encoding="utf-8")
            return True
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

    def _remove_obsolete_html(self) -> None:
        """Remove leftover .html pages from earlier publications."""
        for semester in get_configured_semesters():
            old_page = self.output_dir / f"{semester.replace(' ', '').lower()}.html"
            old_page.unlink(missing_ok=True)
        courses = self.output_dir / "courses"
        if courses.exists():
            for old_page in courses.glob("*/*.html"):
                old_page.unlink()

    def _build_redirects(self) -> str:
        """Build the exact semester .html redirects whose clean targets exist.

        Course pages get no redirect: there is one per course across every
        semester, which grew past the 2,000 static redirect limit and failed the
        whole publish.
        """
        redirects: list[str] = []
        for semester in get_configured_semesters():
            sem_slug = semester_to_slug(semester)
            target = self.output_dir / "semesters" / sem_slug / "index.html"
            if target.is_file():
                old_path = f"/{semester.replace(' ', '').lower()}.html"
                redirects.append(f"{old_path} /semesters/{sem_slug}/ 301")
        return "\n".join(redirects) + ("\n" if redirects else "")

    def _build_headers_content(self) -> str:
        """Build Static Assets headers for generated public output."""
        # Telegram Web frames the Mini App, which X-Frame-Options cannot allow.
        frame_options = (
            ""
            if get_settings().website.miniapp_api_url
            else "  X-Frame-Options: DENY\n"
        )
        return f"""/*
  Cache-Control: public, max-age=0, must-revalidate
  X-Content-Type-Options: nosniff
{frame_options}  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()
  Content-Security-Policy: {content_security_policy(header=True)}
  X-Robots-Tag: noindex, nofollow
/assets/*
  Cache-Control: public, max-age=31536000, immutable

/data/*/manifest.json
  Cache-Control: no-cache

/data/*/manifests/*
  Cache-Control: public, max-age=31536000, immutable

/data/blobs/*
  Cache-Control: public, max-age=31536000, immutable

/data/previews/*
  Cache-Control: public, max-age=31536000, immutable

/courses/*
  Cache-Control: no-cache
"""

    def _build_robots_content(self) -> str:
        """Build robots.txt from the configured indexing directive."""
        return "User-agent: *\nAllow: /\n"

    def _prune_unreferenced_publication_files(self) -> dict[str, int]:
        """Remove immutable files that no current page or rollback pointer uses."""
        removed = {"assets": 0, "blobs": 0, "manifests": 0, "previews": 0}

        referenced_preview_hashes: set[str] = set()
        for page in self.output_dir.rglob("*.html"):
            referenced_preview_hashes |= self._scan_html(page).preview_hashes

        preview_root = self.output_dir / "data" / "previews"
        if preview_root.is_dir():
            for path in preview_root.rglob("*.json"):
                if path.stem not in referenced_preview_hashes:
                    path.unlink()
                    removed["previews"] += 1

        retained_manifests: set[Path] = set()
        retained_blobs: set[Path] = set()
        data_root = self.output_dir / "data"
        for pointer_path in data_root.glob("*/manifest.json"):
            pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
            for key in ("current", "previous"):
                reference = pointer.get(key)
                if not isinstance(reference, str) or not reference:
                    continue
                manifest_path = (pointer_path.parent / reference).resolve()
                if not manifest_path.is_relative_to(data_root.resolve()):
                    raise ValueError(
                        f"manifest reference escapes data root: {reference}"
                    )
                retained_manifests.add(manifest_path)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                references = [
                    manifest.get("summary"),
                    *manifest.get("departments", {}).values(),
                ]
                for item in references:
                    if not isinstance(item, dict) or not isinstance(
                        item.get("url"), str
                    ):
                        continue
                    blob_path = (manifest_path.parent / item["url"]).resolve()
                    if not blob_path.is_relative_to(data_root.resolve()):
                        raise ValueError(
                            f"blob reference escapes data root: {item['url']}"
                        )
                    retained_blobs.add(blob_path)

        for manifests_root in data_root.glob("*/manifests"):
            for path in manifests_root.glob("*.json"):
                if path.resolve() not in retained_manifests:
                    path.unlink()
                    removed["manifests"] += 1
        blobs_root = data_root / "blobs"
        if blobs_root.is_dir():
            for path in blobs_root.glob("*.json"):
                if path.resolve() not in retained_blobs:
                    path.unlink()
                    removed["blobs"] += 1

        asset_root = self.output_dir / "assets"
        vite_manifest = asset_root / ".vite" / "manifest.json"
        if vite_manifest.is_file():
            manifest = json.loads(vite_manifest.read_text(encoding="utf-8"))
            retained_assets = {vite_manifest.resolve()}
            for entry in manifest.values():
                if not isinstance(entry, dict):
                    continue
                references = [
                    entry.get("file"),
                    *entry.get("css", []),
                    *entry.get("assets", []),
                ]
                for reference in references:
                    if isinstance(reference, str):
                        retained_assets.add((asset_root / reference).resolve())
            for path in asset_root.rglob("*"):
                if path.is_file() and path.resolve() not in retained_assets:
                    path.unlink()
                    removed["assets"] += 1

        return removed

    def validate_public_output(self) -> list[str]:
        """Validate that the public output directory contains only allowed files.

        Returns a list of error messages for disallowed artifacts.
        Allowed: HTML, JSON under data/, assets/, _headers, robots.txt,
        courses/, and .checksums.json.
        """
        errors = []
        allowed_extensions = {".html", ".json"}
        allowed_names = {
            "_headers",
            "_redirects",
            "robots.txt",
            ".checksums.json",
            ".assetsignore",
            ".frontend-build-hash",
        }
        allowed_dirs = {"assets", "courses", "data", "previews", "semesters"}
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
            if item.suffix == ".html" and name != "index.html":
                errors.append(
                    f"Obsolete HTML route: {item.relative_to(self.output_dir)}"
                )

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

        errors.extend(self._frontend_asset_reference_issues())
        return errors

    def is_any_semester_active(self, buffer_days: int = 7) -> bool:
        """Check if we are currently within an active registration window."""
        import datetime as dt

        from ..config import get_timezone
        from ..website.config import get_configured_semesters, get_milestones

        registrar_timezone = get_timezone()
        now = dt.datetime.now(registrar_timezone)

        for semester in get_configured_semesters():
            try:
                milestones = get_milestones(semester)
                if not milestones:
                    continue

                times = []
                for milestone in milestones:
                    value = dt.datetime.fromisoformat(milestone["time"])
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=registrar_timezone)
                    else:
                        value = value.astimezone(registrar_timezone)
                    times.append(value)
                earliest = min(times) - dt.timedelta(days=buffer_days)
                latest = max(times) + dt.timedelta(days=buffer_days)

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
        started = time.perf_counter()
        try:
            return self._generate(semester_key, force=force, minify=minify)
        finally:
            elapsed = time.perf_counter() - started
            summary = (
                f"Website generation took {elapsed:.1f}s, "
                f"peak RSS {peak_rss_mb():.0f} MB"
            )
            print(summary, flush=True)
            self.logger.info(summary)

    def _generate(self, semester_key: str | None, *, force: bool, minify: bool) -> bool:
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

            catalog = build_publication_catalog()
            publishable_semesters = [entry.label for entry in catalog]
            if not publishable_semesters:
                print("No configured semesters have a matching stored snapshot.")
                return False

            # Build frontend assets first
            with self._phase("frontend assets"):
                frontend_ready = self.build_frontend_assets()
            if not frontend_ready:
                print("❌ Frontend build failed. Aborting website generation.")
                return False

            root_preview = self.website_assets_dir / "previews" / "root.png"
            if not root_preview.is_file():
                print(f"Error: Missing evergreen preview image: {root_preview}")
                return False
            preview_output = self.output_dir / "previews"
            preview_output.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root_preview, preview_output / "root.png")

            if semester_key:
                # Generate only the specified semester
                key_map = semester_key_map()
                if semester_key not in key_map:
                    print(f"Error: Unknown semester key '{semester_key}'")
                    return False
                semester = key_map[semester_key]
                if semester not in publishable_semesters:
                    print(f"Error: Semester '{semester}' is not publishable")
                    return False
                print(f"Generating website for {semester}...")
                self.generate_semester_page(
                    semester,
                    minify_assets=minify,
                    publication_semesters=publishable_semesters,
                )
            else:
                # Generate all semesters (incremental by default)
                semesters_to_update = get_semesters_needing_update(
                    force=force,
                    checksums_file=self.checksums_file,
                    semesters=publishable_semesters,
                )

                if not semesters_to_update:
                    print("All pages up to date.")
                else:
                    print(f"Generating {len(semesters_to_update)} page(s)...")
                    total_size = 0.0
                    for semester in semesters_to_update:
                        _, size_kb = self.generate_semester_page(
                            semester,
                            minify_assets=minify,
                            publication_semesters=publishable_semesters,
                        )
                        total_size += size_kb

                    print(
                        f"\nGenerated {len(semesters_to_update)} pages ({total_size:.1f} KB total)"
                    )

                # Always regenerate index.html (redirect page)
                index_html = build_redirect_index(publishable_semesters[0])
                index_path = self.output_dir / "index.html"
                write_if_changed(index_path, index_html)
                print("Updated index.html (redirect)")

                # Generate Cloudflare _headers file with security headers
                headers_path = self.output_dir / "_headers"
                write_if_changed(headers_path, self._build_headers_content())
                print("Generated Cloudflare _headers")

                # Keep local state out of the deployed assets
                write_if_changed(
                    self.output_dir / ".assetsignore",
                    ".assetsignore\n.checksums.json\n.frontend-build-hash\n",
                )

                # Generate robots.txt
                robots_path = self.output_dir / "robots.txt"
                write_if_changed(robots_path, self._build_robots_content())
                print("Generated robots.txt")

                self._remove_obsolete_html()
                redirects_path = self.output_dir / "_redirects"
                write_if_changed(redirects_path, self._build_redirects())
                print("Generated narrow .html redirects")

            with self._phase("prune"):
                removed = self._prune_unreferenced_publication_files()
            print(
                "Pruned obsolete immutable files: "
                + ", ".join(f"{count} {kind}" for kind, count in removed.items())
            )

            # Validate public output before any deploy can publish private artifacts.
            with self._phase("validate"):
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

    def deploy(self, *, dry_run: bool = False) -> bool:
        """
        Deploy the generated site and the preview Worker with ``wrangler deploy``.

        The site is the Worker's Static Assets directory (``frontend/public``).
        Wrangler hashes every file and uploads only the ones Cloudflare does not
        already have, so each deploy sends just the changed pages and data.

        With ``dry_run`` the output is still validated and wrangler still
        bundles and checks the Worker, but nothing is uploaded and no
        Cloudflare credentials are needed.

        Returns:
            True if successful
        """
        import os

        if self._output_dir is not None:
            print("❌ Deployment is disabled for isolated generation output.")
            return False

        print("\n🚀 Deploying the site Worker...", flush=True)

        issues = self.validate_public_output()
        if issues:
            print("❌ Cloudflare deploy skipped: public output validation failed.")
            for issue in issues:
                print(f"   - {issue}")
            return False

        # wrangler is a frontend devDependency, so the VM needs no Worker dependencies.
        deploy_cmd = [
            "npx",
            "wrangler",
            "deploy",
            "--config",
            str(WORKER_CONFIG),
            "--strict",
        ]
        if dry_run:
            deploy_cmd.append("--dry-run")

        env = os.environ.copy()
        settings = get_settings()
        if not dry_run:
            if settings.cloudflare_api_token is None:
                print("❌ Cloudflare deploy skipped: CLOUDFLARE_API_TOKEN is not set.")
                print(
                    "   Add CLOUDFLARE_API_TOKEN=... to /home/dmitry_s_ivanenko/registrar_monitor/.env"
                )
                print(
                    "   Settings read .env once at process start, so restart the service afterwards."
                )
                return False
            if settings.cloudflare_account_id is None:
                print("❌ Cloudflare deploy skipped: CLOUDFLARE_ACCOUNT_ID is not set.")
                print(
                    "   Add CLOUDFLARE_ACCOUNT_ID=... to /home/dmitry_s_ivanenko/registrar_monitor/.env"
                )
                print(
                    "   Wrangler custom API tokens can fail account discovery without it."
                )
                print(
                    "   Settings read .env once at process start, so restart the service afterwards."
                )
                return False

            env["CLOUDFLARE_API_TOKEN"] = (
                settings.cloudflare_api_token.get_secret_value()
            )
            env["CLOUDFLARE_ACCOUNT_ID"] = (
                settings.cloudflare_account_id.get_secret_value()
            )
        env["NODE_OPTIONS"] = "--max_old_space_size=512"
        env["CLOUDFLARE_TELEMETRY_DISABLED"] = "1"
        env["NO_UPDATE_NOTIFIER"] = "1"

        try:
            # Inherit stdout/stderr so Wrangler progress reaches the operator and
            # systemd journal immediately. Bound the process so a stalled network
            # upload cannot block the scheduler indefinitely.
            with self._phase("wrangler dry run" if dry_run else "wrangler deploy"):
                result = subprocess.run(
                    deploy_cmd,
                    cwd=self.website_assets_dir,
                    env=env,
                    timeout=DEPLOY_TIMEOUT_SECONDS,
                )
            if result.returncode == 0:
                print(
                    "✅ Dry run succeeded; nothing was uploaded."
                    if dry_run
                    else "✅ Deployment successful!"
                )
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
