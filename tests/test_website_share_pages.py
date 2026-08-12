import json
from unittest.mock import patch

from registrarmonitor.services.website_service import WebsiteService
from registrarmonitor.website.config import course_to_slug, semester_to_slug


def test_clean_route_slugs_are_stable():
    assert semester_to_slug("Fall 2026") == "fall-2026"
    assert course_to_slug("CSCI 101") == "csci-101"
    assert course_to_slug("ANT 214/SOC 214") == "ant-214soc-214"


def test_missing_current_course_route_is_republished_as_removed(tmp_path):
    service = WebsiteService(output_dir=tmp_path)
    route = tmp_path / "courses" / "fall-2026" / "ant-140" / "index.html"
    state_path = tmp_path / "data" / "previews" / "course" / "old.json"
    route.parent.mkdir(parents=True)
    state_path.parent.mkdir(parents=True)
    route.write_text('data-preview-state-url="/data/previews/course/old.json"')
    state_path.write_text(
        json.dumps(
            {
                "kind": "course",
                "semester": "Fall 2026",
                "archived": False,
                "timestamps": ["2026-08-01T09:00:00+05:00"],
                "course": {
                    "code": "ANT 140",
                    "title": "Introduction",
                    "sections": {},
                    "averageHistory": [{"timestampIdx": 0, "fill": 0.5}],
                    "sectionHistory": {},
                    "events": [],
                },
            }
        )
    )

    with patch(
        "registrarmonitor.services.website_service.build_semester_page",
        side_effect=lambda *args, course_state, **kwargs: (
            f'data-preview-state-url="/data/previews/course/{course_state["hash"]}.json"'
        ),
    ):
        count = service._publish_course_routes(
            semester="Fall 2026",
            data={},
            milestones=[],
            departments={},
            semesters=["Fall 2026"],
            archived_semester=False,
            published_at="2026-08-02T09:00:00+05:00",
            minify_assets=False,
        )

    assert count == 1
    published = service._published_course_state(route)
    assert published is not None
    assert published["status"] == "removed"
    assert published["lastChanged"] == "2026-08-01T09:00:00+05:00"
