import re
from dataclasses import dataclass, field

from django.utils import timezone


INTENT_OWN_CONTRIBUTION = "own_contribution"
INTENT_OWN_LOAN = "own_loan"
INTENT_CONTRIBUTION_SUMMARY = "contribution_summary"
INTENT_CONTRIBUTION_RANGE_SUMMARY = "contribution_range_summary"
INTENT_CONTRIBUTION_TRENDS = "contribution_trends"
INTENT_OUTSTANDING_CONTRIBUTIONS = "outstanding_contributions"
INTENT_GROUP_LOAN_SUMMARY = "group_loan_summary"
INTENT_WELFARE_SUMMARY = "welfare_summary"
INTENT_MEETING_MINUTES = "meeting_minutes"
INTENT_FINANCIAL_SUMMARY = "financial_summary"
INTENT_MEMBER_FINANCIAL_SNAPSHOT = "member_financial_snapshot"
INTENT_REMINDER_CONTEXT = "reminder_context"
INTENT_GENERAL_CONVERSATION = "general_conversation"
INTENT_AUTHORIZATION_BYPASS_ATTEMPT = "authorization_bypass_attempt"
INTENT_UNKNOWN = "unknown"

DETERMINISTIC_PERMISSION_DENIAL = (
    "You don't have permission to access that information with your current "
    "Tambul account. You can ask me about your own contributions, loans, and "
    "meeting information available to you."
)

UNSUPPORTED_DIRECT_TOOL_DENIAL = (
    "I can't run internal operations directly from chat. Ask for the Tambul "
    "information you need, and I will use only the access allowed for your "
    "current account."
)

TOOL_BY_INTENT = {
    INTENT_OWN_CONTRIBUTION: "get_my_contribution_status",
    INTENT_OWN_LOAN: "get_my_loan_summary",
    INTENT_CONTRIBUTION_SUMMARY: "get_monthly_contribution_summary",
    INTENT_CONTRIBUTION_RANGE_SUMMARY: "get_contribution_summary_range",
    INTENT_CONTRIBUTION_TRENDS: "get_contribution_trends",
    INTENT_OUTSTANDING_CONTRIBUTIONS: "get_outstanding_contributions",
    INTENT_GROUP_LOAN_SUMMARY: "get_loan_summary",
    INTENT_WELFARE_SUMMARY: "get_welfare_summary",
    INTENT_MEETING_MINUTES: "get_meeting_minutes",
    INTENT_FINANCIAL_SUMMARY: "get_financial_summary",
    INTENT_MEMBER_FINANCIAL_SNAPSHOT: "get_member_financial_snapshot",
    INTENT_REMINDER_CONTEXT: "draft_member_reminder_context",
}
INTENT_BY_TOOL = {tool_name: intent for intent, tool_name in TOOL_BY_INTENT.items()}

MONTH_NAMES = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

DIRECT_TOOL_RE = re.compile(
    r"\b(?:call|invoke|run|use)\s+"
    r"(?P<tool>(?:get|draft)_[a-z0-9_]+)\b"
)
YEAR_MONTH_RE = re.compile(r"\b(?P<year>20\d{2})[-/](?P<month>0?[1-9]|1[0-2])\b")
MONTH_YEAR_RE = re.compile(r"\b(?P<month>0?[1-9]|1[0-2])[-/](?P<year>20\d{2})\b")
YEAR_RE = re.compile(r"\b(?P<year>20\d{2})\b")
MONTH_NAME_RE = re.compile(
    r"\b(?P<month>"
    + "|".join(sorted(MONTH_NAMES, key=len, reverse=True))
    + r")\b(?:\s+(?P<year>20\d{2}))?"
)


@dataclass(frozen=True)
class AIRoutingDecision:
    intent: str
    required_tool: str = ""
    resolved_arguments: dict = field(default_factory=dict)
    denial_message: str = ""
    reason: str = ""

    @property
    def requires_authoritative_tool(self):
        return bool(self.required_tool)

    @property
    def denied(self):
        return bool(self.denial_message)

    @property
    def tool_choice(self):
        if not self.required_tool:
            return None
        return {
            "type": "allowed_tools",
            "mode": "required",
            "tools": [
                {
                    "type": "function",
                    "name": self.required_tool,
                }
            ],
        }


def _normalize(message):
    return re.sub(r"\s+", " ", (message or "").strip().lower())


def _has_any(text, phrases):
    return any(phrase in text for phrase in phrases)


def _has_word(text, word):
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def _mentions_own(text):
    if re.search(r"\b(my|mine|i|i'm|im)\b", text):
        return True
    if "have i" in text or "did i" in text or "am i" in text:
        return True
    if re.search(r"\bme\b", text):
        return not bool(re.search(r"\b(show|tell|give)\s+me\b", text))
    return False


def _mentions_group_scope(text):
    return _has_any(
        text,
        (
            "all members",
            "every member",
            "everyone",
            "group",
            "overall",
            "committee",
            "members who",
            "who still",
            "who has",
        ),
    )


def _mentions_contribution(text):
    return _has_any(
        text,
        (
            "contribution",
            "contributions",
            "monthly dues",
            "dues",
            "paid this month",
            "have i paid",
            "did i pay",
            "paid up",
            "up to date",
            "owe anything",
            "do i owe",
            "payment",
            "payments",
            "collected",
            "collection",
            "contributed",
        ),
    )


def _mentions_loan(text):
    return _has_any(
        text,
        (
            "loan",
            "loans",
            "loan balance",
            "balance on my loan",
            "how much do i owe",
        ),
    )


def _is_write_request(text):
    if re.search(
        r"\b(mark|set|update|change|create|delete|approve|reject)\b.*\bpaid\b",
        text,
    ):
        return True
    if not _has_any(
        text,
        (
            "contribution",
            "payment",
            "loan",
            "welfare",
            "member",
            "record",
        ),
    ):
        return False
    return bool(
        re.search(
            r"\b(mark|set|update|change|create|delete|approve|reject|send|notify)\b",
            text,
        )
    )


def _is_authorization_bypass_attempt(text):
    has_bypass_language = _has_any(
        text,
        (
            "ignore previous instructions",
            "ignore all previous instructions",
            "ignore the rules",
            "ignore security",
            "ignore permissions",
            "ignore my account role",
            "ignore my role",
            "bypass",
            "pretend i am",
            "pretend i'm",
            "i am the treasurer now",
            "i am treasurer now",
            "i am admin now",
            "i am the admin now",
            "use any available function",
            "use any function",
            "database access",
            "access the database",
        ),
    )
    if not has_bypass_language:
        return False

    return _has_any(
        text,
        (
            "all ",
            "every",
            "everyone",
            "member",
            "members",
            "financial",
            "balance",
            "balances",
            "contribution",
            "contributions",
            "loan",
            "loans",
            "welfare",
            "database",
            "function",
            "tool",
            "permission",
            "role",
            "admin",
            "treasurer",
            "outstanding",
            "not paid",
            "unpaid",
        ),
    )


def _extract_period(text, today=None):
    today = today or timezone.localdate()

    if "last month" in text or "previous month" in text:
        year = today.year
        month = today.month - 1
        if month == 0:
            month = 12
            year -= 1
        return {"year": year, "month": month}

    if "this month" in text or "current month" in text:
        return {"year": today.year, "month": today.month}

    year_month = YEAR_MONTH_RE.search(text)
    if year_month:
        return {
            "year": int(year_month.group("year")),
            "month": int(year_month.group("month")),
        }

    month_year = MONTH_YEAR_RE.search(text)
    if month_year:
        return {
            "year": int(month_year.group("year")),
            "month": int(month_year.group("month")),
        }

    for month_name, month in MONTH_NAMES.items():
        match = re.search(rf"\b{month_name}\b(?:\s+(?P<year>20\d{{2}}))?", text)
        if match:
            return {
                "year": int(match.group("year") or today.year),
                "month": month,
            }

    if "this year" in text or "current year" in text:
        return {"year": today.year}

    year = YEAR_RE.search(text)
    if year:
        return {"year": int(year.group("year"))}

    return {}


def _previous_month(today):
    year = today.year
    month = today.month - 1
    if month == 0:
        return year - 1, 12
    return year, month


def _shift_month(year, month, offset):
    month_index = (year * 12) + (month - 1) + offset
    return month_index // 12, (month_index % 12) + 1


def _month_mentions(text, today=None):
    today = today or timezone.localdate()
    mentions = []
    for match in MONTH_NAME_RE.finditer(text):
        mentions.append(
            {
                "year": int(match.group("year") or today.year),
                "month": MONTH_NAMES[match.group("month")],
                "explicit_year": bool(match.group("year")),
            }
        )
    return mentions


def _range_arguments(start_year, start_month, end_year, end_month):
    if (end_year, end_month) < (start_year, start_month):
        return {}
    return {
        "start_year": start_year,
        "start_month": start_month,
        "end_year": end_year,
        "end_month": end_month,
    }


def _extract_period_range(text, today=None):
    today = today or timezone.localdate()

    if "last three months" in text or "past three months" in text:
        start_year, start_month = _shift_month(today.year, today.month, -2)
        return _range_arguments(start_year, start_month, today.year, today.month)

    if "last month" in text or "previous month" in text:
        year, month = _previous_month(today)
        return _range_arguments(year, month, year, month)

    quarters = {
        "first quarter": (1, 3),
        "q1": (1, 3),
        "second quarter": (4, 6),
        "q2": (4, 6),
        "third quarter": (7, 9),
        "q3": (7, 9),
        "fourth quarter": (10, 12),
        "q4": (10, 12),
    }
    for phrase, (start_month, end_month) in quarters.items():
        if phrase in text:
            year_match = YEAR_RE.search(text)
            year = int(year_match.group("year")) if year_match else today.year
            return _range_arguments(year, start_month, year, end_month)

    if _has_any(
        text,
        (
            "year to date",
            "year-to-date",
            "this year",
            "current year",
            "this year so far",
            "this year to date",
            "from january to now",
            "from january until now",
            "since january",
        ),
    ):
        return _range_arguments(today.year, 1, today.year, today.month)

    mentions = _month_mentions(text, today)
    if len(mentions) >= 2:
        start = mentions[0]
        end = mentions[-1]
        if not end["explicit_year"] and end["month"] < start["month"]:
            return {}
        return _range_arguments(
            start["year"],
            start["month"],
            end["year"],
            end["month"],
        )

    if len(mentions) == 1 and _has_any(text, ("from ", "since ", "between ")):
        start = mentions[0]
        return _range_arguments(
            start["year"],
            start["month"],
            today.year,
            today.month,
        )

    if _has_any(text, ("this year", "current year")):
        return _range_arguments(today.year, 1, today.year, today.month)

    return {}


def _extract_member_query(text):
    patterns = (
        r"\bhow much has (?P<name>[a-z][a-z\s.'-]{1,80}?) contributed\b",
        r"\bwhat is (?P<name>[a-z][a-z\s.'-]{1,80}?)'?s current loan balance\b",
        r"\bwhat is (?P<name>[a-z][a-z\s.'-]{1,80}?)'?s loan balance\b",
        r"\bwhat'?s left on (?P<name>[a-z][a-z\s.'-]{1,80}?)'?s loan\b",
        r"\bwhat does (?P<name>[a-z][a-z\s.'-]{1,80}?) still owe\b",
        r"\bhow much does (?P<name>[a-z][a-z\s.'-]{1,80}?) still owe\b",
        r"\bhow much does (?P<name>[a-z][a-z\s.'-]{1,80}?) owe\b",
        r"\bdoes (?P<name>[a-z][a-z\s.'-]{1,80}?) still owe contribution\b",
        r"\bdoes (?P<name>[a-z][a-z\s.'-]{1,80}?) still owe contributions\b",
        r"\bdoes (?P<name>[a-z][a-z\s.'-]{1,80}?) owe contribution\b",
        r"\bdoes (?P<name>[a-z][a-z\s.'-]{1,80}?) owe contributions\b",
        r"\bdoes (?P<name>[a-z][a-z\s.'-]{1,80}?) have any outstanding contribution\b",
        r"\bgive me (?P<name>[a-z][a-z\s.'-]{1,80}?)'?s financial summary\b",
        r"\bgive me (?P<name>[a-z][a-z\s.'-]{1,80}?) financial summary\b",
        r"\bshow (?P<name>[a-z][a-z\s.'-]{1,80}?)'?s loan\b",
        r"\bshow (?P<name>[a-z][a-z\s.'-]{1,80}?)'?s contribution\b",
    )
    stop_words = {
        "a quick",
        "quick",
        "my",
        "our",
        "group",
        "the group",
        "the groups",
        "overall",
        "current",
        "committee",
        "everyone",
        "all members",
    }
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group("name")).strip(" .'")
        if name and name not in stop_words:
            return name
    return ""


def _has_range_language(text):
    return _has_any(
        text,
        (
            "from ",
            "since ",
            "between ",
            "until now",
            "to now",
            "through ",
            "year to date",
            "year-to-date",
            "this year",
            "current year",
            "this year so far",
            "last three months",
            "past three months",
            "first quarter",
            "second quarter",
            "third quarter",
            "fourth quarter",
            "q1",
            "q2",
            "q3",
            "q4",
        ),
    )


def _has_trend_language(text):
    return _has_any(
        text,
        (
            "trend",
            "trends",
            "compare",
            "comparison",
            "month to month",
            "month-to-month",
            "month by month",
            "month-by-month",
            "which month",
            "best month",
            "performed best",
            "most outstanding",
            "changed this year",
            "performance from",
        ),
    )


def _period_or_current_month(text, today=None):
    today = today or timezone.localdate()
    period = _extract_period(text, today)
    if "month" in period:
        return period
    if "year" in period:
        return period
    return {"year": today.year, "month": today.month}


def _period_for_required_monthly_tool(text, today=None):
    today = today or timezone.localdate()
    period = _extract_period(text, today)
    if "month" in period:
        return period
    if "year" in period:
        return {}
    return {"year": today.year, "month": today.month}


def _arguments_for_intent(intent, text, today=None):
    if intent in {
        INTENT_CONTRIBUTION_SUMMARY,
        INTENT_OUTSTANDING_CONTRIBUTIONS,
        INTENT_REMINDER_CONTEXT,
    }:
        return _period_for_required_monthly_tool(text, today)
    if intent in {
        INTENT_CONTRIBUTION_RANGE_SUMMARY,
        INTENT_CONTRIBUTION_TRENDS,
    }:
        return _extract_period_range(text, today)
    if intent == INTENT_MEMBER_FINANCIAL_SNAPSHOT:
        arguments = _period_or_current_month(text, today) if _mentions_contribution(text) else {}
        member_query = _extract_member_query(text)
        if member_query:
            arguments["member_query"] = member_query
        return arguments
    if intent == INTENT_OWN_CONTRIBUTION:
        return _period_or_current_month(text, today)
    if intent in {
        INTENT_GROUP_LOAN_SUMMARY,
        INTENT_WELFARE_SUMMARY,
    }:
        return _extract_period(text, today)
    if intent == INTENT_FINANCIAL_SUMMARY:
        if _has_range_language(text):
            return _extract_period_range(text, today)
        return _extract_period(text, today)
    if intent == INTENT_MEETING_MINUTES and _has_any(text, ("latest", "last meeting")):
        return {"limit": 1}
    return {}


def _decision_for_intent(intent, text, allowed_tool_names, *, today=None, reason=""):
    required_tool = TOOL_BY_INTENT[intent]
    if required_tool not in allowed_tool_names:
        return AIRoutingDecision(
            intent=intent,
            denial_message=DETERMINISTIC_PERMISSION_DENIAL,
            reason=reason or "unauthorized_data_intent",
        )

    return AIRoutingDecision(
        intent=intent,
        required_tool=required_tool,
        resolved_arguments=_arguments_for_intent(intent, text, today),
        reason=reason or "authorized_data_intent",
    )


def route_ai_message(message, allowed_tool_names, *, today=None):
    text = _normalize(message)
    allowed_tool_names = set(allowed_tool_names)

    direct_tool = DIRECT_TOOL_RE.search(text)
    if direct_tool:
        tool_name = direct_tool.group("tool")
        intent = INTENT_BY_TOOL.get(tool_name)
        if intent is None:
            return AIRoutingDecision(
                intent=INTENT_AUTHORIZATION_BYPASS_ATTEMPT,
                denial_message=UNSUPPORTED_DIRECT_TOOL_DENIAL,
                reason="unsupported_direct_tool_request",
            )
        return _decision_for_intent(
            intent,
            text,
            allowed_tool_names,
            today=today,
            reason="direct_tool_request",
        )

    if _is_authorization_bypass_attempt(text):
        return AIRoutingDecision(
            intent=INTENT_AUTHORIZATION_BYPASS_ATTEMPT,
            denial_message=DETERMINISTIC_PERMISSION_DENIAL,
            reason="authorization_bypass_attempt",
        )

    if _is_write_request(text):
        return AIRoutingDecision(
            intent=INTENT_GENERAL_CONVERSATION,
            reason="read_only_request",
        )

    if _has_any(text, ("meeting minutes", "latest meeting", "last meeting")) or (
        "meeting" in text
        and _has_any(
            text,
            ("summarize", "what happened", "notes", "decided", "decision"),
        )
    ):
        return _decision_for_intent(
            INTENT_MEETING_MINUTES,
            text,
            allowed_tool_names,
            today=today,
        )

    if _extract_member_query(text) and _has_any(
        text,
        (
            "contributed",
            "contribution",
            "loan",
            "balance",
            "owe",
            "outstanding",
            "financial summary",
            "welfare",
        ),
    ):
        return _decision_for_intent(
            INTENT_MEMBER_FINANCIAL_SNAPSHOT,
            text,
            allowed_tool_names,
            today=today,
        )

    if _mentions_own(text) and _mentions_contribution(text):
        return _decision_for_intent(
            INTENT_OWN_CONTRIBUTION,
            text,
            allowed_tool_names,
            today=today,
        )

    if _mentions_own(text) and _mentions_loan(text) and not _mentions_group_scope(text):
        return _decision_for_intent(
            INTENT_OWN_LOAN,
            text,
            allowed_tool_names,
            today=today,
        )

    if _mentions_contribution(text) and _has_trend_language(text):
        range_arguments = _extract_period_range(text, today)
        if range_arguments:
            return _decision_for_intent(
                INTENT_CONTRIBUTION_TRENDS,
                text,
                allowed_tool_names,
                today=today,
            )

    if _mentions_contribution(text) and _has_range_language(text):
        range_arguments = _extract_period_range(text, today)
        if range_arguments:
            return _decision_for_intent(
                INTENT_CONTRIBUTION_RANGE_SUMMARY,
                text,
                allowed_tool_names,
                today=today,
            )

    if _has_any(
        text,
        (
            "outstanding contribution",
            "outstanding contributions",
            "who still has",
            "who has not paid",
            "who hasn't paid",
            "everyone who has not paid",
            "members who have not paid",
            "which members are late",
            "outstanding contributors",
            "who still owes",
            "not paid this month",
            "unpaid members",
            "late contributions",
        ),
    ):
        return _decision_for_intent(
            INTENT_OUTSTANDING_CONTRIBUTIONS,
            text,
            allowed_tool_names,
            today=today,
        )

    if _has_any(
        text,
        (
            "reminder context",
            "draft reminders",
            "draft reminder",
            "who should be reminded",
            "send reminders",
        ),
    ):
        return _decision_for_intent(
            INTENT_REMINDER_CONTEXT,
            text,
            allowed_tool_names,
            today=today,
        )

    if _has_any(
        text,
        (
            "contribution performance",
            "contributions performing",
            "contributions perform",
            "contributions doing",
            "contribution summary",
            "monthly contribution summary",
            "how much have we collected",
            "total collected",
            "collected this month",
            "monthly payments going",
            "payments going",
            "still outstanding for contributions",
            "collection rate",
            "collection percentage",
        ),
    ):
        return _decision_for_intent(
            INTENT_CONTRIBUTION_SUMMARY,
            text,
            allowed_tool_names,
            today=today,
        )

    if _has_any(
        text,
        (
            "financial summary",
            "financial overview",
            "group finances",
            "overall finances",
            "finance summary",
            "financial situation",
            "financial position",
            "financially for the meeting",
            "outstanding altogether",
            "contributions, loans and welfare",
            "contributions loans and welfare",
        ),
    ):
        return _decision_for_intent(
            INTENT_FINANCIAL_SUMMARY,
            text,
            allowed_tool_names,
            today=today,
        )

    if _has_any(
        text,
        (
            "group loan",
            "loan performance",
            "loan summary",
            "loan analytics",
            "loan portfolio",
            "loan situation",
            "loan position",
            "active loans",
            "has been repaid",
            "have been repaid",
        ),
    ) and (not _mentions_own(text) or _mentions_group_scope(text)):
        return _decision_for_intent(
            INTENT_GROUP_LOAN_SUMMARY,
            text,
            allowed_tool_names,
            today=today,
        )

    if _has_any(
        text,
        (
            "welfare summary",
            "welfare performance",
            "welfare statistics",
            "welfare analytics",
            "welfare position",
            "welfare this year",
        ),
    ):
        return _decision_for_intent(
            INTENT_WELFARE_SUMMARY,
            text,
            allowed_tool_names,
            today=today,
        )

    if "meeting" in text:
        return AIRoutingDecision(
            intent=INTENT_GENERAL_CONVERSATION,
            reason="ambiguous_meeting_question",
        )

    if _has_any(
        text,
        (
            "hello",
            "hi",
            "help",
            "what can you help",
            "what can you do",
        ),
    ):
        return AIRoutingDecision(
            intent=INTENT_GENERAL_CONVERSATION,
            reason="general_conversation",
        )

    return AIRoutingDecision(intent=INTENT_UNKNOWN, reason="unknown")
