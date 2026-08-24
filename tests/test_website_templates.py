import json

from registrarmonitor.website.templates import (
    _build_nav_html,
    build_redirect_index,
    build_semester_page,
)


def manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"src/main.js": {"file": "main-test.js", "css": ["main-test.css"]}})
    )
    return path


def course_state(*, archived=False):
    return {
        "hash": "obLD1OX2",
        "kind": "course",
        "semester": "Fall 2026",
        "semesterSlug": "fall-2026",
        "slug": "ant-140",
        "code": "ANT 140",
        "title": "Introduction to Anthropology",
        "status": "removed" if archived else "current",
        "archived": archived,
        "availability": {
            "sentence": "1 registration place available. Limited by labs.",
        },
        "priority": {
            "compact": "P2 · Y4+",
            "full": "Priority 2 - Year 4+",
            "current": {
                "label": "Y4+",
                "time": "2026-08-13T09:00:00+05:00",
                "priority": "2",
            },
            "next": {
                "label": "Y3",
                "time": "2026-08-13T11:00:00+05:00",
                "priority": "2",
            },
        },
    }


def test_navigation_uses_publishable_clean_semester_routes():
    html = _build_nav_html("Fall 2026", ["Fall 2026", "Summer 2026", "Spring 2026"])

    assert 'href="/semesters/fall-2026/"' in html
    assert 'href="/semesters/summer-2026/"' in html
    assert html.count('aria-current="page"') == 1


def test_semester_shell_uses_root_absolute_assets_and_versioned_metadata(tmp_path):
    html = build_semester_page(
        {"cr": {"ANT 140": {}}, "lrt": "2026-08-12T10:00:00+05:00"},
        [],
        "Fall 2026",
        manifest_path=manifest(tmp_path),
        semesters=["Fall 2026"],
        preview_state={"hash": "obLD1OX2"},
    )

    assert 'href="/assets/main-test.css"' in html
    assert 'src="/assets/main-test.js"' in html
    assert 'data-manifest-url="/data/fall-2026/manifest.json"' in html
    assert 'title="Literal elapsed time"' in html
    assert 'id="registrationUnavailableGuide"' in html
    assert "Required sections full" in html
    assert "long gaps shortened" not in html
    assert "/semesters/fall-2026/?v=" not in html
    assert '<meta name="robots" content="noindex, nofollow">' in html


def test_semester_shell_exposes_portable_telegram_bookmark_import(tmp_path):
    html = build_semester_page(
        {"cr": {"ANT 140": {}}, "lrt": None},
        [],
        "Fall 2026",
        manifest_path=manifest(tmp_path),
        semesters=["Fall 2026"],
        preview_state={"hash": "obLD1OX2"},
    )

    assert 'data-semester="Fall 2026"' in html
    assert 'id="telegramBookmarkImport"' in html
    assert "Copy for bot" in html
    assert html.index('class="filter-buttons"') < html.index(
        '</div>\n\n            <button type="button" class="telegram-import-btn"'
    )


def test_course_shell_opens_existing_modal_and_uses_clean_canonical(tmp_path):
    html = build_semester_page(
        {"cr": {}, "lrt": "2026-08-12T10:00:00+05:00"},
        [],
        "Fall 2026",
        manifest_path=manifest(tmp_path),
        semesters=["Fall 2026"],
        course_state=course_state(),
    )

    assert 'data-initial-course="ANT 140"' in html
    assert 'data-preview-state-url="/data/previews/course/obLD1OX2.json"' in html
    assert (
        '<link rel="canonical" href="https://registrar-monitor.pages.dev/courses/fall-2026/ant-140/">'
        in html
    )
    assert "/courses/fall-2026/ant-140/?v=obLD1OX2" in html
    assert (
        "https://registrar-monitor-preview-images.spooktaken.workers.dev/"
        "preview/course/fall-2026/ant-140/obLD1OX2.png"
    ) in html
    assert (
        'content="Introduction to Anthropology. 1 registration place available. '
        'Limited by labs."' in html
    )


def test_archived_course_uses_unversioned_og_url(tmp_path):
    html = build_semester_page(
        {"cr": {}, "lrt": None},
        [],
        "Fall 2026",
        manifest_path=manifest(tmp_path),
        semesters=["Fall 2026"],
        course_state=course_state(archived=True),
    )

    assert 'data-page-archived="true"' in html
    assert (
        'content="https://registrar-monitor.pages.dev/courses/fall-2026/ant-140/"'
        in html
    )
    assert "/courses/fall-2026/ant-140/?v=" not in html


def test_archived_semester_uses_unversioned_og_url_and_page_state(tmp_path):
    html = build_semester_page(
        {"cr": {}, "lrt": None},
        [],
        "Spring 2026",
        manifest_path=manifest(tmp_path),
        semesters=["Fall 2026", "Spring 2026"],
        preview_state={
            "hash": "obLD1OX2",
            "archived": True,
            "courseCount": 391,
            "sectionCount": 846,
            "fullSectionCount": 142,
        },
    )

    assert 'data-page-archived="true"' in html
    assert (
        'content="https://registrar-monitor.pages.dev/semesters/spring-2026/"' in html
    )
    assert "/semesters/spring-2026/?v=" not in html
    assert "142 sections were full at the final update" in html


def test_root_has_evergreen_metadata_and_visible_fallback():
    html = build_redirect_index("Fall 2026")

    assert "<title>Enrollment Monitor</title>" in html
    assert "See historical and frequently updated undergraduate course data" in html
    assert "Nazarbayev" not in html
    assert 'content="noindex, nofollow"' in html
    assert 'href="/semesters/fall-2026/"' in html
    assert "/previews/root.png" in html
