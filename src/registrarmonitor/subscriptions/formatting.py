"""Personal Telegram digest formatting."""

from ..models import EnrollmentComparison, EnrollmentSnapshot
from ..reporting.report_formatter import ReportFormatter
from ..reporting.telegram_formatting import render_report_chunks
from .matching import filter_comparison
from .models import SubscriptionTarget


def render_personal_digest(
    comparison: EnrollmentComparison,
    current: EnrollmentSnapshot,
    previous: EnrollmentSnapshot,
    targets: list[SubscriptionTarget],
) -> list[str]:
    """Render one user's matching enrollment changes as Telegram chunks."""
    formatter = ReportFormatter()
    filtered = filter_comparison(comparison, targets)
    if not formatter.has_reportable_changes(filtered):
        return []
    report = formatter.format_changes_report(filtered, current, previous)
    return render_report_chunks(f"Your watches - {current.semester}\n{report}")
