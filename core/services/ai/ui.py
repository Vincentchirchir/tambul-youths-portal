from django.conf import settings

from core.permissions import can_view_committee_minutes, is_committee_user

from .access import can_use_ai_assistant
from .tool_policy import available_ai_tool_names


PROMPTS_BY_TOOL = (
    ("get_my_contribution_status", "Have I paid my contribution this month?"),
    ("get_my_contribution_status", "What is my current contribution balance?"),
    ("get_my_loan_summary", "Show me my loan summary."),
    ("get_meeting_minutes", "What are the latest meeting minutes I can view?"),
    ("get_monthly_contribution_summary", "How are contributions performing this month?"),
    ("get_outstanding_contributions", "How much is currently outstanding?"),
    ("get_loan_summary", "Give me a group loan summary."),
    ("get_welfare_summary", "Summarize welfare activity this year."),
    ("get_financial_summary", "Give me a financial summary for this month."),
)

COMMITTEE_PROMPTS_BY_TOOL = (
    ("get_monthly_contribution_summary", "How are contributions doing this month?"),
    ("get_contribution_summary_range", "How are contributions from January to now?"),
    ("get_outstanding_contributions", "Who still has outstanding contributions?"),
    ("get_loan_summary", "Give me the current loan summary."),
    ("get_welfare_summary", "Give me the welfare summary for this year."),
    ("get_financial_summary", "Give me the group financial summary."),
    ("get_meeting_minutes", "Summarize the latest committee meeting."),
)


def ai_assistant_suggested_prompts(user):
    available_tools = set(available_ai_tool_names(user))
    prompt_source = (
        COMMITTEE_PROMPTS_BY_TOOL
        if is_committee_user(user)
        else PROMPTS_BY_TOOL
    )
    prompts = [
        prompt
        for tool_name, prompt in prompt_source
        if tool_name in available_tools
    ]
    if (
        not is_committee_user(user)
        and "get_meeting_minutes" in available_tools
        and can_view_committee_minutes(user)
    ):
        prompts.append("Summarize the latest committee meeting.")

    deduplicated = []
    for prompt in prompts:
        if prompt not in deduplicated:
            deduplicated.append(prompt)
    return deduplicated


def ai_assistant_ui_context(user):
    enabled = can_use_ai_assistant(user)
    return {
        "ai_assistant_enabled": enabled,
        "ai_assistant_max_input_length": int(
            getattr(settings, "AI_MAX_INPUT_LENGTH", 1000)
        ),
        "ai_assistant_prompts": (
            ai_assistant_suggested_prompts(user)
            if enabled
            else []
        ),
    }
