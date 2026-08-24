"""State-addressed links shared outside the generated dashboard."""

from registrarmonitor.website.share import published_course_share_url


def test_published_course_share_url_uses_generated_preview_hash(tmp_path):
    route = tmp_path / "courses" / "spring-2024" / "cs-101" / "index.html"
    route.parent.mkdir(parents=True)
    route.write_text(
        '<body data-preview-hash="Abcd_123" data-page-archived="false"></body>',
        encoding="utf-8",
    )

    assert published_course_share_url("Spring 2024", "CS 101", output_dir=tmp_path) == (
        "https://registrar-monitor.pages.dev/courses/spring-2024/cs-101/?v=Abcd_123"
    )


def test_published_course_share_url_rejects_missing_or_archived_state(tmp_path):
    assert (
        published_course_share_url("Spring 2024", "CS 101", output_dir=tmp_path) is None
    )

    route = tmp_path / "courses" / "spring-2024" / "cs-101" / "index.html"
    route.parent.mkdir(parents=True)
    route.write_text(
        '<body data-preview-hash="Abcd_123" data-page-archived="true"></body>',
        encoding="utf-8",
    )

    assert (
        published_course_share_url("Spring 2024", "CS 101", output_dir=tmp_path) is None
    )
