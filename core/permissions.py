from django.core.exceptions import PermissionDenied


ROLE_ADMIN = "admin"
ROLE_CHAIRPERSON = "chairperson"
ROLE_VICE_CHAIRPERSON = "vice-chairperson"
ROLE_COMMITTEE = "committee"
ROLE_TREASURER = "treasurer"
ROLE_SECRETARY = "secretary"
ROLE_VICE_SECRETARY = "vice-secretary"
ROLE_COORDINATOR = "coordinator"
ROLE_WELFARE = "welfare"
ROLE_MEMBER = "member"

ROLE_CHOICES = [
    (ROLE_ADMIN, "Admin"),
    (ROLE_CHAIRPERSON, "Chairperson"),
    (ROLE_VICE_CHAIRPERSON, "Vice-Chairperson"),
    (ROLE_COMMITTEE, "Committee"),
    (ROLE_TREASURER, "Treasurer"),
    (ROLE_SECRETARY, "Secretary"),
    (ROLE_VICE_SECRETARY, "Vice-Secretary"),
    (ROLE_COORDINATOR, "Coordinator"),
    (ROLE_WELFARE, "Welfare"),
    (ROLE_MEMBER, "Member"),
]

ROLE_GROUP_NAMES = dict(ROLE_CHOICES)

COMMITTEE_ROLES = frozenset(
    {
        ROLE_ADMIN,
        ROLE_CHAIRPERSON,
        ROLE_VICE_CHAIRPERSON,
        ROLE_COMMITTEE,
        ROLE_TREASURER,
        ROLE_SECRETARY,
        ROLE_VICE_SECRETARY,
        ROLE_COORDINATOR,
        ROLE_WELFARE,
    }
)

LETTER_REVIEW_ROLES = frozenset(
    {
        ROLE_ADMIN,
        ROLE_CHAIRPERSON,
        ROLE_VICE_CHAIRPERSON,
        ROLE_SECRETARY,
        ROLE_VICE_SECRETARY,
    }
)
LETTER_APPROVER_ROLES = frozenset({ROLE_CHAIRPERSON})
LETTER_ISSUER_ROLES = frozenset({ROLE_CHAIRPERSON})

CONTRIBUTION_SUMMARY_ROLES = frozenset(COMMITTEE_ROLES)
OUTSTANDING_CONTRIBUTION_ROLES = frozenset(COMMITTEE_ROLES)
GROUP_LOAN_SUMMARY_ROLES = frozenset(COMMITTEE_ROLES)
INDIVIDUAL_MEMBER_FINANCIAL_ROLES = frozenset(COMMITTEE_ROLES)
WELFARE_SUMMARY_ROLES = frozenset(COMMITTEE_ROLES)
GROUP_FINANCIAL_ANALYTICS_ROLES = frozenset(COMMITTEE_ROLES)


def user_role(user):
    return getattr(user, "role", "")


def is_authenticated_user(user):
    return bool(getattr(user, "is_authenticated", False))


def has_role(user, roles):
    return is_authenticated_user(user) and user_role(user) in roles


def same_user(user, member):
    return bool(
        is_authenticated_user(user)
        and member is not None
        and getattr(user, "pk", None) == getattr(member, "pk", None)
    )


def ensure_allowed(allowed, message="You are not authorized to perform this action."):
    if not allowed:
        raise PermissionDenied(message)


def group_name_for_role(role):
    return ROLE_GROUP_NAMES.get(role)


def is_committee_user(user):
    return has_role(user, COMMITTEE_ROLES)


def can_access_committee_dashboard(user):
    return is_committee_user(user)


def can_view_own_financial_information(user):
    return is_authenticated_user(user)


def can_view_member_financial_records(user, member):
    return same_user(user, member) or has_role(user, INDIVIDUAL_MEMBER_FINANCIAL_ROLES)


def can_view_contribution_summaries(user):
    return has_role(user, CONTRIBUTION_SUMMARY_ROLES)


def can_view_individual_outstanding_contributions(user):
    return has_role(user, OUTSTANDING_CONTRIBUTION_ROLES)


def can_view_group_loan_summaries(user):
    return has_role(user, GROUP_LOAN_SUMMARY_ROLES)


def can_view_welfare_summaries(user):
    return has_role(user, WELFARE_SUMMARY_ROLES)


def can_view_group_financial_analytics(user):
    return has_role(user, GROUP_FINANCIAL_ANALYTICS_ROLES)


def can_view_committee_minutes(user):
    return is_committee_user(user)


def can_view_general_minutes(user):
    return is_authenticated_user(user)


def can_view_meeting_note(user, note):
    if not is_authenticated_user(user):
        return False
    if getattr(note, "audience", None) == "committee_only":
        return can_view_committee_minutes(user)
    return can_view_general_minutes(user)


def can_export_contributions_csv(user):
    return has_role(
        user,
        {ROLE_ADMIN, ROLE_CHAIRPERSON, ROLE_TREASURER, ROLE_SECRETARY, ROLE_COMMITTEE},
    )


def can_export_contributions_pdf(user):
    return has_role(user, {ROLE_ADMIN, ROLE_CHAIRPERSON, ROLE_TREASURER, ROLE_COMMITTEE})


def can_export_welfare(user):
    return has_role(
        user,
        {ROLE_ADMIN, ROLE_CHAIRPERSON, ROLE_TREASURER, ROLE_WELFARE, ROLE_COMMITTEE},
    )


def can_export_loans(user):
    return has_role(
        user,
        {ROLE_ADMIN, ROLE_CHAIRPERSON, ROLE_TREASURER, ROLE_SECRETARY, ROLE_COMMITTEE},
    )


def can_update_loan_approval(user):
    return has_role(user, {ROLE_CHAIRPERSON})


def can_update_loan_repayments(user):
    return has_role(user, {ROLE_CHAIRPERSON})


def can_update_contributions(user):
    return has_role(user, {ROLE_TREASURER})


def can_update_welfare(user):
    return has_role(user, {ROLE_WELFARE})


def can_create_announcements(user):
    return has_role(user, {ROLE_COORDINATOR})


def can_post_meeting_notes(user):
    return has_role(user, {ROLE_SECRETARY})


def can_view_payment_intent(user, payment_intent):
    return same_user(user, payment_intent.member) or has_role(
        user, {ROLE_ADMIN, ROLE_CHAIRPERSON, ROLE_TREASURER}
    )
