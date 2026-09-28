from core.permissions import (
    can_view_contribution_summaries,
    can_view_group_financial_analytics,
    can_view_group_loan_summaries,
    can_view_individual_outstanding_contributions,
    can_view_own_financial_information,
    can_view_welfare_summaries,
    is_authenticated_user,
    is_committee_user,
)
from core.services.ai_tools import AI_TOOL_REGISTRY


def available_ai_tool_names(user):
    if not is_authenticated_user(user):
        return ()

    names = [
        "get_current_user_context",
        "get_meeting_minutes",
    ]

    if can_view_own_financial_information(user):
        names.extend(
            [
                "get_my_contribution_status",
                "get_my_loan_summary",
            ]
        )
    if can_view_contribution_summaries(user):
        names.extend(
            [
                "get_monthly_contribution_summary",
                "get_contribution_summary_range",
                "get_contribution_trends",
            ]
        )
    if can_view_individual_outstanding_contributions(user):
        names.extend(
            [
                "get_outstanding_contributions",
                "draft_member_reminder_context",
            ]
        )
    if can_view_group_loan_summaries(user):
        names.append("get_loan_summary")
    if can_view_welfare_summaries(user):
        names.append("get_welfare_summary")
    if can_view_group_financial_analytics(user):
        names.append("get_financial_summary")
    if is_committee_user(user):
        names.append("get_member_financial_snapshot")

    return tuple(name for name in names if name in AI_TOOL_REGISTRY)


def available_ai_tool_definitions(user):
    return [
        AI_TOOL_REGISTRY[name]
        for name in available_ai_tool_names(user)
    ]
