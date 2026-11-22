"""Offline substitutes at the role boundary; never open inference connections."""

from datetime import UTC, datetime

from portfolio_management.types import RoleContext, TeamResult


def fixed_clock() -> datetime:
    return datetime(2026, 10, 31, 12, tzinfo=UTC)


class RecordingLead:
    def __init__(self, context: RoleContext):
        self.context = context

    async def invoke_async(self, question: str) -> TeamResult:
        snapshot = self.context.request.portfolio
        detail = snapshot.snapshot_id if snapshot else "no portfolio supplied"
        return TeamResult(
            summary=question,
            report_markdown=f"# Scripted research\n\n{question}\n\nSnapshot: {detail}\n",
        )


def recording_factory(role: str, context: RoleContext) -> RecordingLead:
    assert role == "team_lead"
    return RecordingLead(context)
