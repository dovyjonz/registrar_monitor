"""Tests for the scheduler module (beyond heat decay)."""

from datetime import datetime


from registrarmonitor.automation.scheduler import (
    SchedulingDecision,
    SchedulingLevel,
    get_current_zone_type,
    parse_schedule_file,
)


class TestSchedulingLevel:
    """Tests for SchedulingLevel enum."""

    def test_level_labels(self):
        """Scheduling levels should have expected string labels."""
        assert SchedulingLevel.EXTREME.label == "extreme"
        assert SchedulingLevel.HIGH.label == "high"
        assert SchedulingLevel.MODERATE.label == "moderate"
        assert SchedulingLevel.LOW.label == "low"

    def test_level_intervals(self):
        """Scheduling levels should have expected intervals."""
        assert SchedulingLevel.EXTREME.interval == 12
        assert SchedulingLevel.HIGH.interval == 120
        assert SchedulingLevel.MODERATE.interval == 300
        assert SchedulingLevel.LOW.interval == 1200

    def test_from_label(self):
        """Should create level from string label."""
        assert SchedulingLevel.from_label("extreme") == SchedulingLevel.EXTREME
        assert SchedulingLevel.from_label("high") == SchedulingLevel.HIGH
        assert SchedulingLevel.from_label("moderate") == SchedulingLevel.MODERATE
        assert SchedulingLevel.from_label("low") == SchedulingLevel.LOW

    def test_from_score(self):
        """Should create level from activity score."""
        assert SchedulingLevel.from_score(50.0) == SchedulingLevel.EXTREME
        assert SchedulingLevel.from_score(30.0) == SchedulingLevel.EXTREME
        assert SchedulingLevel.from_score(15.0) == SchedulingLevel.HIGH
        assert SchedulingLevel.from_score(10.0) == SchedulingLevel.HIGH
        assert SchedulingLevel.from_score(5.0) == SchedulingLevel.MODERATE
        assert SchedulingLevel.from_score(1.0) == SchedulingLevel.MODERATE
        assert SchedulingLevel.from_score(0.5) == SchedulingLevel.LOW
        assert SchedulingLevel.from_score(0.0) == SchedulingLevel.LOW

    def test_is_more_urgent_than(self):
        """Should correctly compare urgency levels."""
        assert SchedulingLevel.EXTREME.is_more_urgent_than(SchedulingLevel.HIGH)
        assert SchedulingLevel.HIGH.is_more_urgent_than(SchedulingLevel.MODERATE)
        assert SchedulingLevel.MODERATE.is_more_urgent_than(SchedulingLevel.LOW)
        assert not SchedulingLevel.LOW.is_more_urgent_than(SchedulingLevel.HIGH)


class TestParseScheduleFile:
    """Tests for parse_schedule_file function."""

    def test_valid_schedule_file(self, tmp_path):
        """Valid schedule file should be parsed correctly."""
        schedule_content = """# Comment line
extreme,2024-01-15 09:00,2024-01-15 12:00
high,2024-01-15 13:00,2024-01-15 17:00
"""
        schedule_file = tmp_path / "schedule.txt"
        schedule_file.write_text(schedule_content)

        result = parse_schedule_file(str(schedule_file))

        assert SchedulingLevel.EXTREME in result
        assert SchedulingLevel.HIGH in result
        assert len(result[SchedulingLevel.EXTREME]) == 1
        assert len(result[SchedulingLevel.HIGH]) == 1

    def test_empty_schedule_file(self, tmp_path):
        """Empty schedule file should return empty zones."""
        schedule_file = tmp_path / "schedule.txt"
        schedule_file.write_text("")

        result = parse_schedule_file(str(schedule_file))

        assert result[SchedulingLevel.EXTREME] == []
        assert result[SchedulingLevel.HIGH] == []
        assert result[SchedulingLevel.MODERATE] == []

    def test_missing_schedule_file(self):
        """Missing schedule file should return empty zones."""
        result = parse_schedule_file("/nonexistent/schedule.txt")

        assert SchedulingLevel.EXTREME in result
        assert SchedulingLevel.HIGH in result
        assert SchedulingLevel.MODERATE in result
        assert result[SchedulingLevel.EXTREME] == []
        assert result[SchedulingLevel.HIGH] == []
        assert result[SchedulingLevel.MODERATE] == []

    def test_comments_ignored(self, tmp_path):
        """Comment lines should be ignored."""
        schedule_content = """# This is a comment
# Another comment
extreme,2024-01-15 09:00,2024-01-15 12:00
"""
        schedule_file = tmp_path / "schedule.txt"
        schedule_file.write_text(schedule_content)

        result = parse_schedule_file(str(schedule_file))

        assert len(result[SchedulingLevel.EXTREME]) == 1

    def test_invalid_lines_skipped(self, tmp_path):
        """Invalid lines should be skipped gracefully."""
        schedule_content = """extreme,2024-01-15 09:00,2024-01-15 12:00
invalid_line_without_commas
another,bad,line,too,many,parts
high,2024-01-15 13:00,2024-01-15 17:00
"""
        schedule_file = tmp_path / "schedule.txt"
        schedule_file.write_text(schedule_content)

        result = parse_schedule_file(str(schedule_file))

        # Should have parsed the valid lines
        assert len(result[SchedulingLevel.EXTREME]) == 1
        assert len(result[SchedulingLevel.HIGH]) == 1


class TestGetCurrentZoneType:
    """Tests for get_current_zone_type function."""

    def test_low_when_no_schedule(self):
        """Should return LOW when no schedule file exists."""
        result = get_current_zone_type("/nonexistent/schedule.txt")
        assert result == SchedulingLevel.LOW

    def test_extreme_zone_active(self, tmp_path):
        """Should return EXTREME when in extreme zone time."""
        now = datetime.now()
        start = now.replace(hour=0, minute=0)
        end = now.replace(hour=23, minute=59)

        schedule_content = f"extreme,{start.strftime('%Y-%m-%d %H:%M')},{end.strftime('%Y-%m-%d %H:%M')}\n"
        schedule_file = tmp_path / "schedule.txt"
        schedule_file.write_text(schedule_content)

        result = get_current_zone_type(str(schedule_file))
        assert result == SchedulingLevel.EXTREME

    def test_high_zone_active(self, tmp_path):
        """Should return HIGH when in high zone time."""
        now = datetime.now()
        start = now.replace(hour=0, minute=0)
        end = now.replace(hour=23, minute=59)

        schedule_content = f"high,{start.strftime('%Y-%m-%d %H:%M')},{end.strftime('%Y-%m-%d %H:%M')}\n"
        schedule_file = tmp_path / "schedule.txt"
        schedule_file.write_text(schedule_content)

        result = get_current_zone_type(str(schedule_file))
        assert result == SchedulingLevel.HIGH

    def test_moderate_zone_active(self, tmp_path):
        """Should return MODERATE when in moderate zone time."""
        now = datetime.now()
        start = now.replace(hour=0, minute=0)
        end = now.replace(hour=23, minute=59)

        schedule_content = f"moderate,{start.strftime('%Y-%m-%d %H:%M')},{end.strftime('%Y-%m-%d %H:%M')}\n"
        schedule_file = tmp_path / "schedule.txt"
        schedule_file.write_text(schedule_content)

        result = get_current_zone_type(str(schedule_file))
        assert result == SchedulingLevel.MODERATE


class TestSchedulingDecision:
    """Tests for SchedulingDecision dataclass."""

    def test_to_dict(self):
        """SchedulingDecision should serialize to dictionary."""
        decision = SchedulingDecision(
            timestamp=datetime(2024, 1, 15, 10, 30),
            change_score=5.0,
            current_heat=10.0,
            baseline_level=SchedulingLevel.LOW,
            reactive_level=SchedulingLevel.HIGH,
            final_level=SchedulingLevel.HIGH,
            final_interval=120,
        )

        result = decision.to_dict()

        assert result["change_score"] == 5.0
        assert result["current_heat"] == 10.0
        assert result["final_level"] == "high"
        assert result["final_interval_seconds"] == 120
        assert result["baseline_level"] == "low"

    def test_timestamp_serialization(self):
        """Timestamp should be serialized as ISO format string."""
        decision = SchedulingDecision(
            timestamp=datetime(2024, 1, 15, 10, 30, 45),
            change_score=0.0,
            current_heat=0.0,
            baseline_level=SchedulingLevel.LOW,
            reactive_level=SchedulingLevel.LOW,
            final_level=SchedulingLevel.LOW,
            final_interval=1200,
        )

        result = decision.to_dict()

        assert "2024-01-15" in result["timestamp"]
        assert "10:30" in result["timestamp"]
