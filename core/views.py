from django.views.generic import TemplateView, CreateView, ListView, View, DetailView
from django.views.generic.edit import FormView, UpdateView
from django.urls import reverse_lazy, reverse
from datetime import date
from decimal import Decimal, InvalidOperation
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Case, When, Value, Count, Q
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from.models import (
    CommitteeLetter,
    CommitteeLetterAudit,
    Contribution,
    Loan,
    Welfare,
    Announcement,
    MeetingNote,
    Notification,
)
from .forms import (
    CommitteeLetterCommentForm,
    CommitteeLetterForm,
    CommitteeLetterReturnForm,
    ContributionForm,
    LetterVerificationForm,
    LoanApplicationForm,
    AnnouncementForm,
    MeetingNoteForm,
)
from django.shortcuts import render, redirect, get_object_or_404
import requests
from django.conf import settings
from accounts.models import User
from accounts.forms import ProfileForm
from django.db.models.functions import TruncMonth
import csv
from django .contrib import messages
from django.http import FileResponse, HttpResponse, HttpResponseForbidden
from django.core.files.base import ContentFile
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from django.contrib.staticfiles import finders
from reportlab.lib.utils import ImageReader
import json
import calendar
from django.utils import timezone
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.contrib.auth.mixins import PermissionRequiredMixin
from .services.notifications import (
    committee_users,
    normalize_notification_link,
    notify_users,
)
from .services.committee_letters import (
    committee_letter_pdf_filename,
    generate_committee_letter_pdf,
    letter_render_context,
)
from .services.committee_letter_workflow import (
    can_approve_letters,
    can_edit_letter,
    can_issue_letters,
    can_review_letters,
    approve_letter,
    cancel_letter,
    create_correction_draft,
    ensure_allowed,
    is_committee_user,
    issue_letter,
    record_audit,
    return_letter,
    submit_letter,
)
from .models import LetterAuditAction
from django.templatetags.static import static

COMMITTEE_ROLES = {
    "chairperson",
    "vice-chairperson",
    "treasurer",
    "secretary",
    "vice-secretary",
    "welfare",
    "coordinator",
    "admin",
    "committee",
}

LETTER_REVIEW_ROLES = {
    "chairperson",
    "vice-chairperson",
    "secretary",
    "vice-secretary",
    "admin",
}

LETTER_APPROVER_ROLES = {
    "chairperson",
}

LETTER_ISSUER_ROLES = {
    "chairperson",
}


def dashboard_year_options():
    current_year = timezone.localdate().year
    years = {current_year}
    date_sources = (
        (Contribution.objects, "month"),
        (Loan.objects, "loan_date"),
        (Welfare.objects, "date_given"),
    )

    for queryset, field_name in date_sources:
        years.update(value.year for value in queryset.dates(field_name, "year"))

    return sorted(years, reverse=True)


def selected_dashboard_period(request):
    current_year = timezone.localdate().year
    available_years = dashboard_year_options()
    raw_year = request.GET.get("year")
    raw_month = request.GET.get("month")

    try:
        year = int(raw_year) if raw_year else current_year
    except (TypeError, ValueError):
        year = current_year

    if year not in available_years:
        available_years = sorted({*available_years, year}, reverse=True)

    try:
        month = int(raw_month) if raw_month else None
    except (TypeError, ValueError):
        month = None

    if month not in range(1, 13):
        month = None

    return year, month, available_years


def filter_by_dashboard_period(queryset, field_name, year, month=None):
    filters = {f"{field_name}__year": year}
    if month:
        filters[f"{field_name}__month"] = month
    return queryset.filter(**filters)


def dashboard_param(request, name):
    return (request.GET.get(name) or "").strip()


def filter_text(queryset, value, lookups):
    if not value:
        return queryset

    query = Q()
    for lookup in lookups:
        query |= Q(**{lookup: value})
    return queryset.filter(query)


def filter_exact(queryset, field_name, value):
    if not value:
        return queryset
    return queryset.filter(**{field_name: value})


def filter_month_value(queryset, field_name, value):
    if not value:
        return queryset
    try:
        year, month = [int(part) for part in value.split("-", 1)]
    except (TypeError, ValueError):
        return queryset
    if month not in range(1, 13):
        return queryset
    return queryset.filter(**{f"{field_name}__year": year, f"{field_name}__month": month})


def filter_decimal_range(queryset, field_name, minimum="", maximum=""):
    try:
        if minimum:
            queryset = queryset.filter(**{f"{field_name}__gte": Decimal(minimum)})
        if maximum:
            queryset = queryset.filter(**{f"{field_name}__lte": Decimal(maximum)})
    except (InvalidOperation, ValueError):
        return queryset
    return queryset


def filter_date_range(queryset, field_name, start="", end=""):
    try:
        if start:
            queryset = queryset.filter(**{f"{field_name}__date__gte": date.fromisoformat(start)})
        if end:
            queryset = queryset.filter(**{f"{field_name}__date__lte": date.fromisoformat(end)})
    except ValueError:
        return queryset
    return queryset


def dashboard_filter_values(request):
    names = [
        "member_search",
        "member_role",
        "loan_member",
        "loan_status",
        "loan_repayment_status",
        "loan_month",
        "loan_due_month",
        "loan_min_amount",
        "loan_max_amount",
        "loan_has_balance",
        "contribution_member",
        "contribution_status",
        "contribution_month",
        "contribution_min_amount",
        "contribution_max_amount",
        "welfare_member",
        "welfare_status",
        "welfare_month",
        "welfare_min_amount",
        "welfare_max_amount",
        "letter_search",
        "letter_recipient_type",
        "letter_type",
        "letter_status",
        "letter_created_by",
        "announcement_search",
        "announcement_from",
        "announcement_to",
        "minute_search",
        "minute_from",
        "minute_to",
    ]
    return {name: dashboard_param(request, name) for name in names}


def dashboard_period_context(year, month, available_years):
    month_options = [
        {"value": number, "label": calendar.month_name[number]}
        for number in range(1, 13)
    ]
    period_label = f"{calendar.month_name[month]} {year}" if month else str(year)
    return {
        "this_year": year,
        "selected_year": year,
        "selected_month": month,
        "available_years": available_years,
        "month_options": month_options,
        "period_label": period_label,
    }


def web_manifest(request):
    manifest = {
        "name": "Tambul Hustle Youth Group",
        "short_name": "Tambul Hustle",
        "description": "Member portal for Tambul Hustle Youth Group.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui", "browser"],
        "background_color": "#041127",
        "theme_color": "#1b6ef2",
        "icons": [
            {
                "src": static("images/logo.png"),
                "type": "image/png",
                "sizes": "640x640",
                "purpose": "any maskable",
            }
        ],
    }
    return HttpResponse(
        json.dumps(manifest),
        content_type="application/manifest+json",
    )


def service_worker(request):
    worker_script = """self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', () => {
  // Online-only app: no offline cache strategy.
});
"""
    response = HttpResponse(worker_script, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response

class Index(TemplateView):
    template_name="core/index.html"

class MemberDashboardView(LoginRequiredMixin, TemplateView):
    template_name="core/member_dashboard.html"
    def get_context_data(self, **kwargs):
        ctx=super().get_context_data(**kwargs)
        user=self.request.user
        year, month, available_years = selected_dashboard_period(self.request)
        member_contributions = filter_by_dashboard_period(
            Contribution.objects.filter(member=user),
            "month",
            year,
            month,
        )
        member_loans = filter_by_dashboard_period(
            Loan.objects.filter(member=user),
            "loan_date",
            year,
            month,
        )
        member_welfare = filter_by_dashboard_period(
            Welfare.objects.filter(member=user),
            "date_given",
            year,
            month,
        )

        ctx["unread_notifications"] = user.notifications.filter(is_read=False).count()

        ctx["contrib_ytd"] = (
            member_contributions.filter(status__in=["fully_paid", "partially_paid"])
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )

        period_active_loans = member_loans.filter(status__in=["pending", "approved"])

        ctx["active_loan_count"] = period_active_loans.count()


        ctx["outstanding_principal"]=(
            period_active_loans.aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["loan_balance"] = sum(loan.current_balance() for loan in period_active_loans)
        ctx["today"]=date.today()

        ctx["can_apply_loan"] = not Loan.objects.filter(
            member=user,
            repayment_status__in=["not_paid", "partially_paid", "late"],
            status__in=["pending", "approved"],
        ).exists()

        ctx["loans"] = member_loans.order_by("-created_at")
        ctx["today"] = date.today()


        ctx["recent_contributions"]=(
            member_contributions.order_by("-month", "-created_at")[:12]
        )


        ctx["recent_loans"]=(
            member_loans.order_by("-created_at")[:5]
        )

        ctx["recent_welfare"] =(
            member_welfare.order_by("-date_given")[:5]
        )

        ctx["latest_announcements"] = (
            filter_by_dashboard_period(Announcement.objects.all(), "published_at", year, month)
            .order_by("-published_at")[:5]
        )

        ctx["meeting_notes"] = (
            filter_by_dashboard_period(
                MeetingNote.objects.filter(audience=MeetingNote.AUDIENCE_ALL),
                "created_at",
                year,
                month,
            ).order_by("-created_at")
        )


        ctx.update(dashboard_period_context(year, month, available_years))
        return ctx

class CommitteeDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "core/committee_dashboard.html"

    #  Access Control 
    def test_func(self):
        return self.request.user.role in COMMITTEE_ROLES

    # Context Data
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year, month, available_years = selected_dashboard_period(self.request)
        user = self.request.user
        filters = dashboard_filter_values(self.request)
        ctx["user_display_name"] = user.first_name or user.username
        ctx["unread_notifications"] = user.notifications.filter(is_read=False).count()

        #  1️ OVERVIEW PART
        ctx["total_members"] = User.objects.exclude(role="admin").count()
        loans_for_period = filter_by_dashboard_period(Loan.objects.all(), "loan_date", year, month)
        approved_loans_for_period = loans_for_period.filter(status="approved")
        contributions_for_period = filter_by_dashboard_period(
            Contribution.objects.all(),
            "month",
            year,
            month,
        )
        welfare_for_period = filter_by_dashboard_period(
            Welfare.objects.all(),
            "date_given",
            year,
            month,
        )
        ctx["total_contributions"] = (
            contributions_for_period.filter(status__in=["fully_paid", "partially_paid"])
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )
        ctx["total_loans"] = approved_loans_for_period.count()
        ctx["pending_loans"] = loans_for_period.filter(status="pending").count()
        ctx["total_loan_disbursed"] = (
            approved_loans_for_period.aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["total_loan_repaid"] = (
            approved_loans_for_period.aggregate(total=Sum("total_paid_so_far"))["total"] or 0
        )
        ctx["total_loan_outstanding"] = sum(
            loan.current_balance() for loan in approved_loans_for_period
        )
        ctx["total_welfare"] = (
            welfare_for_period.filter(status__in=["fully_paid", "partially_paid"])
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )

        # Personal summary metrics for the logged-in committee member
        personal_contributions_for_period = filter_by_dashboard_period(
            Contribution.objects.filter(member=user),
            "month",
            year,
            month,
        )
        personal_loans_for_period = filter_by_dashboard_period(
            Loan.objects.filter(member=user),
            "loan_date",
            year,
            month,
        )
        personal_welfare_for_period = filter_by_dashboard_period(
            Welfare.objects.filter(member=user),
            "date_given",
            year,
            month,
        )
        announcements_for_period = filter_by_dashboard_period(
            Announcement.objects.all(),
            "published_at",
            year,
            month,
        )
        meeting_notes_for_period = filter_by_dashboard_period(
            MeetingNote.objects.all(),
            "created_at",
            year,
            month,
        )

        ctx["my_contrib_ytd"] = (
            personal_contributions_for_period.filter(status__in=["fully_paid", "partially_paid"])
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )

        personal_active_loans = personal_loans_for_period.filter(status__in=["pending", "approved"])

        ctx["my_active_loan_count"] = personal_active_loans.count()
        ctx["my_outstanding_principal"] = (
            personal_active_loans.aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["my_loan_balance"] = sum(
            loan.current_balance() for loan in personal_active_loans
            if loan.current_balance()
        )

                #Check if committee member can apply loan
        ctx["can_apply_loan"] = not Loan.objects.filter(
            member=user,
            repayment_status__in=["not_paid", "partially_paid", "late"],
            status__in=["pending", "approved"],
        ).exists()

        ctx["my_loans"] = personal_loans_for_period.order_by("-created_at")
        ctx["my_contributions"] = (
            personal_contributions_for_period.order_by("-month", "-created_at")[:6]
        )
        ctx["my_welfare"] = (
            personal_welfare_for_period.order_by("-date_given")[:6]
        )
        ctx["my_welfare_total"] = (
            personal_welfare_for_period.aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["latest_announcements"] = (
            announcements_for_period.order_by("-published_at")[:5]
        )
        ctx["today"] = date.today()

        #2️ DATA TABLES
        members = User.objects.exclude(role="admin")
        members = filter_text(
            members,
            filters["member_search"],
            [
                "first_name__icontains",
                "last_name__icontains",
                "username__icontains",
                "email__icontains",
                "phone__icontains",
                "membership_number__icontains",
                "national_id__icontains",
            ],
        )
        members = filter_exact(members, "role", filters["member_role"])

        loans = loans_for_period.select_related("member")
        loans = filter_text(
            loans,
            filters["loan_member"],
            [
                "member__first_name__icontains",
                "member__last_name__icontains",
                "member__username__icontains",
                "member__membership_number__icontains",
            ],
        )
        loans = filter_exact(loans, "status", filters["loan_status"])
        loans = filter_exact(loans, "repayment_status", filters["loan_repayment_status"])
        loans = filter_month_value(loans, "loan_date", filters["loan_month"])
        loans = filter_month_value(loans, "due_date", filters["loan_due_month"])
        loans = filter_decimal_range(
            loans,
            "amount",
            filters["loan_min_amount"],
            filters["loan_max_amount"],
        )
        loans = list(loans.order_by("-created_at"))
        if filters["loan_has_balance"] == "yes":
            loans = [loan for loan in loans if loan.status != "rejected" and loan.current_balance() > 0]
        elif filters["loan_has_balance"] == "no":
            loans = [loan for loan in loans if loan.status == "rejected" or loan.current_balance() == 0]
        filtered_approved_loans = [loan for loan in loans if loan.status == "approved"]

        contributions = contributions_for_period.select_related("member")
        contributions = filter_text(
            contributions,
            filters["contribution_member"],
            [
                "member__first_name__icontains",
                "member__last_name__icontains",
                "member__username__icontains",
                "member__membership_number__icontains",
            ],
        )
        contributions = filter_exact(contributions, "status", filters["contribution_status"])
        contributions = filter_month_value(contributions, "month", filters["contribution_month"])
        contributions = filter_decimal_range(
            contributions,
            "amount",
            filters["contribution_min_amount"],
            filters["contribution_max_amount"],
        )
        filtered_contribution_total = (
            contributions.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        )

        welfare_records = welfare_for_period.select_related("member")
        welfare_records = filter_text(
            welfare_records,
            filters["welfare_member"],
            [
                "member__first_name__icontains",
                "member__last_name__icontains",
                "member__username__icontains",
                "member__membership_number__icontains",
                "description__icontains",
            ],
        )
        welfare_records = filter_exact(welfare_records, "status", filters["welfare_status"])
        welfare_records = filter_month_value(welfare_records, "date_given", filters["welfare_month"])
        welfare_records = filter_decimal_range(
            welfare_records,
            "amount",
            filters["welfare_min_amount"],
            filters["welfare_max_amount"],
        )

        ctx["members"] = members.order_by("first_name", "last_name", "username")
        ctx["loans"] = loans
        ctx["filtered_loan_disbursed"] = sum(
            (loan.amount for loan in filtered_approved_loans),
            Decimal("0.00"),
        )
        ctx["filtered_loan_repaid"] = sum(
            (loan.total_paid_so_far for loan in filtered_approved_loans),
            Decimal("0.00"),
        )
        ctx["filtered_loan_outstanding"] = sum(
            (loan.current_balance() for loan in filtered_approved_loans),
            Decimal("0.00"),
        )
        ctx["contributions"] = contributions.order_by("-month", "-created_at")
        ctx["filtered_contribution_total"] = filtered_contribution_total
        ctx["welfare_records"] = welfare_records.order_by("-date_given")

        # 3️ ANNOUNCEMENTS 
        announcements = filter_text(
            announcements_for_period,
            filters["announcement_search"],
            ["title__icontains", "message__icontains"],
        )
        announcements = filter_date_range(
            announcements,
            "published_at",
            filters["announcement_from"],
            filters["announcement_to"],
        )
        ctx["announcements"] = announcements.order_by("-published_at")[:10]
        ctx["latest_announcements"] = announcements.order_by("-published_at")[:5]

        #4️ ANALYTICS DATA
        # Monthly contribution
        monthly = (
            contributions_for_period.filter(status__in=["fully_paid", "partially_paid"])
            .annotate(month_label=TruncMonth("month"))
            .values("month_label")
            .annotate(total=Sum("amount"))
            .order_by("month_label")
        )

        ctx["monthly_labels"] = [m["month_label"].strftime("%b") for m in monthly]
        ctx["monthly_values"] = [float(m["total"]) for m in monthly]

        # Loan repayment distribution for approved loans
        repayment_order = [
            ("not_paid", "Not Paid"),
            ("partially_paid", "Partially Paid"),
            ("late", "Late"),
            ("fully_paid", "Fully Paid"),
        ]
        approved_loans_for_analytics = approved_loans_for_period
        loan_stats = {
            key: {"count": 0, "total": 0.0}
            for key, _ in repayment_order
        }

        for loan in approved_loans_for_analytics:
            status_key = loan.repayment_status
            if status_key not in loan_stats:
                continue

            loan_stats[status_key]["count"] += 1
            if status_key == "fully_paid":
                figure = float(loan.total_paid_so_far or 0)
            else:
                figure = float(loan.current_balance() or 0)
            loan_stats[status_key]["total"] += figure

        ctx["loan_labels"] = [label for _, label in repayment_order]
        ctx["loan_counts"] = [loan_stats.get(key, {}).get("count", 0) for key, _ in repayment_order]
        ctx["loan_totals"] = [loan_stats.get(key, {}).get("total", 0) for key, _ in repayment_order]

        # Welfare totals by status
        welfare_stats = (
            welfare_for_period
            .values("status")
            .annotate(total=Sum("amount"))
        )
        ctx["welfare_labels"] = [w["status"] for w in welfare_stats]
        ctx["welfare_totals"] = [float(w["total"]) for w in welfare_stats]
        #Meeting Reports
        meeting_notes = filter_text(
            meeting_notes_for_period,
            filters["minute_search"],
            ["title__icontains", "description__icontains", "content__icontains"],
        )
        meeting_notes = filter_date_range(
            meeting_notes,
            "created_at",
            filters["minute_from"],
            filters["minute_to"],
        )
        ctx["meeting_notes"] = meeting_notes.order_by("-created_at")

        # 5️ YEAR & DATE INFO
        ctx.update(dashboard_period_context(year, month, available_years))
        ctx["today"] = date.today()
        ctx["filters"] = filters
        ctx["role_choices"] = User.ROLE_CHOICES
        ctx["loan_status_choices"] = Loan._meta.get_field("status").choices
        ctx["loan_repayment_status_choices"] = Loan._meta.get_field("repayment_status").choices
        ctx["contribution_status_choices"] = Contribution._meta.get_field("status").choices
        ctx["welfare_status_choices"] = Welfare._meta.get_field("status").choices
        ctx["letter_type_choices"] = CommitteeLetter.LETTER_TYPE_CHOICES
        ctx["letter_status_choices"] = CommitteeLetter.STATUS_CHOICES
        ctx["letter_recipient_type_choices"] = CommitteeLetter.RECIPIENT_TYPE_CHOICES

        # Top 5 members by approved loan amount this year
        top_borrowers = (
            approved_loans_for_period
            .values("member__username")
            .annotate(total=Sum("amount"))
            .order_by("-total")[:5]
        )
        ctx["top_contributors"] = [b["member__username"] for b in top_borrowers]
        ctx["top_contrib_values"] = [float(b["total"]) for b in top_borrowers]

        # Pre-serialize chart data for front-end consumption.
        ctx["chart_datasets"] = {
            "monthlyLabels": ctx["monthly_labels"],
            "monthlyValues": ctx["monthly_values"],
            "loanLabels": ctx["loan_labels"],
            "loanCounts": ctx["loan_counts"],
            "loanTotals": ctx["loan_totals"],
            "welfareLabels": ctx["welfare_labels"],
            "welfareTotals": ctx["welfare_totals"],
            "topContributors": ctx["top_contributors"],
            "topContribValues": ctx["top_contrib_values"],
        }
        ctx["chart_datasets_json"] = json.dumps(ctx["chart_datasets"])

        letters = (
            CommitteeLetter.objects.select_related(
                "created_by",
                "approved_by",
                "signatory",
            )
            .all()
        )
        letters = filter_text(
            letters,
            filters["letter_search"],
            [
                "reference_number__icontains",
                "subject__icontains",
                "recipient_name__icontains",
                "institution_name__icontains",
                "attention_name__icontains",
                "signatory_name__icontains",
            ],
        )
        letters = filter_exact(letters, "recipient_type", filters["letter_recipient_type"])
        letters = filter_exact(letters, "letter_type", filters["letter_type"])
        letters = filter_exact(letters, "status", filters["letter_status"])
        letters = filter_text(
            letters,
            filters["letter_created_by"],
            [
                "created_by__first_name__icontains",
                "created_by__last_name__icontains",
                "created_by__username__icontains",
            ],
        )
        status_totals = {
            row["status"]: row["total"]
            for row in letters.values("status").annotate(total=Count("id"))
        }
        ctx["letter_form"] = CommitteeLetterForm()
        ctx["committee_letters"] = letters.order_by("-created_at")[:50]
        ctx["letter_status_cards"] = [
            {
                "status": status,
                "label": label,
                "total": status_totals.get(status, 0),
            }
            for status, label in CommitteeLetter.STATUS_CHOICES
        ]
        ctx["can_review_letters"] = user.role in LETTER_REVIEW_ROLES
        ctx["can_approve_letters"] = user.role in LETTER_APPROVER_ROLES
        ctx["can_issue_letters"] = user.role in LETTER_ISSUER_ROLES

        return ctx


class CommitteeLetterAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return is_committee_user(self.request.user)

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return HttpResponseForbidden("You are not authorized to access committee letters.")
        return super().handle_no_permission()


def committee_letters_redirect():
    return reverse("committee-letter-list")


def get_committee_letter(pk):
    return get_object_or_404(
        CommitteeLetter.objects.select_related(
            "created_by",
            "reviewed_by",
            "approved_by",
            "signatory",
            "supersedes",
        ),
        pk=pk,
    )


def save_official_pdf(letter, actor):
    pdf_bytes = generate_committee_letter_pdf(letter)
    letter.pdf_file.save(
        committee_letter_pdf_filename(letter),
        ContentFile(pdf_bytes),
        save=True,
    )
    record_audit(
        letter,
        actor,
        LetterAuditAction.PDF_GENERATED,
        letter.status,
        letter.status,
    )


class CommitteeLetterListView(CommitteeLetterAccessMixin, TemplateView):
    template_name = "core/committee_letters/list.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        letters = CommitteeLetter.objects.select_related(
            "created_by",
            "approved_by",
        ).order_by("-created_at")
        status_totals = {
            row["status"]: row["total"]
            for row in letters.values("status").annotate(total=Count("id"))
        }
        ctx["letters"] = letters[:100]
        ctx["total_letters"] = letters.count()
        ctx["status_cards"] = [
            {
                "status": status,
                "label": label,
                "total": status_totals.get(status, 0),
            }
            for status, label in CommitteeLetter.STATUS_CHOICES
        ]
        ctx["recent_letters"] = letters[:8]
        ctx["can_review_letters"] = can_review_letters(self.request.user)
        ctx["can_approve_letters"] = can_approve_letters(self.request.user)
        ctx["can_issue_letters"] = can_issue_letters(self.request.user)
        return ctx


class CommitteeLetterCreateView(CommitteeLetterAccessMixin, CreateView):
    model = CommitteeLetter
    form_class = CommitteeLetterForm
    template_name = "core/committee_letters/form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        record_audit(
            self.object,
            self.request.user,
            LetterAuditAction.CREATED,
            status_to=self.object.status,
        )
        messages.success(self.request, f"Letter {self.object.reference_number} created.")
        return response

    def get_success_url(self):
        return reverse("committee-letter-detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page_title"] = "Create Committee Letter"
        return ctx


class CommitteeLetterEditView(CommitteeLetterAccessMixin, UpdateView):
    model = CommitteeLetter
    form_class = CommitteeLetterForm
    template_name = "core/committee_letters/form.html"

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        ensure_allowed(can_edit_letter(request.user, self.object))
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        record_audit(
            self.object,
            self.request.user,
            LetterAuditAction.EDITED,
            self.object.status,
            self.object.status,
        )
        messages.success(self.request, f"Letter {self.object.reference_number} updated.")
        return response

    def get_success_url(self):
        return reverse("committee-letter-detail", args=[self.object.pk])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page_title"] = f"Edit {self.object.reference_number}"
        return ctx


class CommitteeLetterDetailView(CommitteeLetterAccessMixin, DetailView):
    model = CommitteeLetter
    template_name = "core/committee_letters/detail.html"
    context_object_name = "letter"

    def get_queryset(self):
        return CommitteeLetter.objects.select_related(
            "created_by",
            "reviewed_by",
            "approved_by",
            "signatory",
            "supersedes",
        ).prefetch_related("audit_entries")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        letter = self.object
        ctx["history"] = letter.audit_entries.select_related("actor")
        ctx["can_edit"] = can_edit_letter(self.request.user, letter)
        ctx["can_review"] = can_review_letters(self.request.user)
        ctx["can_approve"] = can_approve_letters(self.request.user)
        ctx["can_issue"] = can_issue_letters(self.request.user)
        return ctx


class CommitteeLetterPreviewView(CommitteeLetterAccessMixin, DetailView):
    model = CommitteeLetter
    template_name = "core/committee_letters/pdf_template.html"
    context_object_name = "letter"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(letter_render_context(self.object, official=self.object.is_approved_for_pdf))
        return ctx


class CommitteeLetterActionView(CommitteeLetterAccessMixin, View):
    template_name = "core/committee_letters/confirm.html"
    form_class = CommitteeLetterCommentForm
    action = ""
    title = ""
    button_label = ""

    def get_letter(self):
        return get_committee_letter(self.kwargs["pk"])

    def get_form_class(self):
        if self.action == "return":
            return CommitteeLetterReturnForm
        return self.form_class

    def ensure_action_allowed(self, request, letter):
        return None

    def get(self, request, *args, **kwargs):
        letter = self.get_letter()
        try:
            self.ensure_action_allowed(request, letter)
        except PermissionDenied:
            return HttpResponseForbidden("You are not authorized to perform this action.")
        form = self.get_form_class()()
        return render(
            request,
            self.template_name,
            {
                "letter": letter,
                "form": form,
                "action": self.action,
                "title": self.title,
                "button_label": self.button_label,
            },
        )

    def post(self, request, *args, **kwargs):
        letter = self.get_letter()
        try:
            self.ensure_action_allowed(request, letter)
        except PermissionDenied:
            return HttpResponseForbidden("You are not authorized to perform this action.")
        form = self.get_form_class()(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "letter": letter,
                    "form": form,
                    "action": self.action,
                    "title": self.title,
                    "button_label": self.button_label,
                },
            )

        try:
            self.perform_action(letter, form.cleaned_data.get("comment", ""))
        except PermissionDenied:
            return HttpResponseForbidden("You are not authorized to perform this action.")
        except ValidationError as exc:
            messages.warning(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
            return redirect("committee-letter-detail", pk=letter.pk)

        return redirect("committee-letter-detail", pk=letter.pk)

    def perform_action(self, letter, comment):
        raise NotImplementedError


class CommitteeLetterSubmitView(CommitteeLetterActionView):
    action = "submit"
    title = "Submit Letter"
    button_label = "Submit for Approval"

    def ensure_action_allowed(self, request, letter):
        ensure_allowed(can_edit_letter(request.user, letter))

    def perform_action(self, letter, comment):
        submit_letter(letter, self.request.user)
        messages.success(self.request, f"{letter.reference_number} submitted for approval.")


class CommitteeLetterReturnView(CommitteeLetterActionView):
    action = "return"
    title = "Return Letter"
    button_label = "Return for Correction"

    def ensure_action_allowed(self, request, letter):
        ensure_allowed(can_review_letters(request.user))

    def perform_action(self, letter, comment):
        return_letter(letter, self.request.user, comment)
        messages.success(self.request, f"{letter.reference_number} returned for correction.")


class CommitteeLetterApproveView(CommitteeLetterActionView):
    action = "approve"
    title = "Approve Letter"
    button_label = "Approve and Generate PDF"

    def ensure_action_allowed(self, request, letter):
        ensure_allowed(can_approve_letters(request.user))

    def perform_action(self, letter, comment):
        approve_letter(letter, self.request.user)
        letter.refresh_from_db()
        save_official_pdf(letter, self.request.user)
        if letter.created_by:
            notify_users(
                recipients=[letter.created_by],
                title="Committee Letter Approved",
                message=f"Letter {letter.reference_number} has been approved.",
                link=reverse("committee-letter-detail", args=[letter.pk]),
                send_email=True,
            )
        messages.success(self.request, f"{letter.reference_number} approved and PDF generated.")


class CommitteeLetterIssueView(CommitteeLetterActionView):
    action = "issue"
    title = "Issue Letter"
    button_label = "Issue Letter"

    def ensure_action_allowed(self, request, letter):
        ensure_allowed(can_issue_letters(request.user))

    def perform_action(self, letter, comment):
        issue_letter(letter, self.request.user)
        letter.refresh_from_db()
        save_official_pdf(letter, self.request.user)
        messages.success(self.request, f"{letter.reference_number} issued.")


class CommitteeLetterCancelView(CommitteeLetterActionView):
    action = "cancel"
    title = "Cancel Letter"
    button_label = "Cancel Letter"

    def ensure_action_allowed(self, request, letter):
        ensure_allowed(can_review_letters(request.user))

    def perform_action(self, letter, comment):
        cancel_letter(letter, self.request.user, comment)
        messages.success(self.request, f"{letter.reference_number} cancelled.")


class CommitteeLetterCorrectView(CommitteeLetterActionView):
    action = "correct"
    title = "Create Correction Draft"
    button_label = "Create Draft Version"

    def ensure_action_allowed(self, request, letter):
        ensure_allowed(is_committee_user(request.user))

    def perform_action(self, letter, comment):
        correction = create_correction_draft(letter, self.request.user)
        messages.success(
            self.request,
            f"Correction draft {correction.reference_number} created.",
        )


class CommitteeLetterGeneratePDFView(CommitteeLetterActionView):
    action = "generate_pdf"
    title = "Generate Official PDF"
    button_label = "Generate PDF"

    def ensure_action_allowed(self, request, letter):
        ensure_allowed(can_approve_letters(request.user) or can_issue_letters(request.user))

    def perform_action(self, letter, comment):
        ensure_allowed(can_approve_letters(self.request.user) or can_issue_letters(self.request.user))
        if not letter.is_approved_for_pdf:
            raise ValidationError("Only approved or issued letters can generate official PDFs.")
        save_official_pdf(letter, self.request.user)
        messages.success(self.request, f"PDF generated for {letter.reference_number}.")


class CommitteeLetterPDFView(CommitteeLetterAccessMixin, View):
    def get(self, request, pk):
        letter = get_committee_letter(pk)
        if not letter.is_approved_for_pdf:
            return HttpResponseForbidden("Only approved or issued official PDFs can be downloaded.")
        if letter.status == CommitteeLetter.STATUS_APPROVED and can_approve_letters(request.user):
            save_official_pdf(letter, request.user)
            letter.refresh_from_db()
        if not letter.pdf_file:
            ensure_allowed(can_approve_letters(request.user) or can_issue_letters(request.user))
            save_official_pdf(letter, request.user)
            letter.refresh_from_db()
        return FileResponse(
            letter.pdf_file.open("rb"),
            as_attachment=True,
            filename=committee_letter_pdf_filename(letter),
            content_type="application/pdf",
        )


class CommitteeLetterStatusUpdateView(CommitteeLetterAccessMixin, View):
    legacy_action_map = {
        "submit": submit_letter,
        "correct": create_correction_draft,
    }

    def post(self, request, pk, action):
        letter = get_committee_letter(pk)
        try:
            if action == "submit":
                submit_letter(letter, request.user)
            elif action == "correct":
                create_correction_draft(letter, request.user)
            else:
                raise ValidationError("Use the dedicated workflow confirmation page for this action.")
        except PermissionDenied:
            return HttpResponseForbidden("You are not authorized to perform this action.")
        except ValidationError as exc:
            messages.warning(request, exc.messages[0] if hasattr(exc, "messages") else str(exc))
        return redirect("committee-letter-list")


class LetterVerifyView(FormView):
    template_name = "core/committee_letters/verify.html"
    form_class = LetterVerificationForm

    def form_valid(self, form):
        code = form.cleaned_data["verification_code"].strip()
        return redirect("letter-verify-result", verification_code=code)


class LetterVerifyResultView(TemplateView):
    template_name = "core/committee_letters/verify_result.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        code = kwargs["verification_code"].strip()
        letter = CommitteeLetter.objects.filter(verification_code__iexact=code).first()
        ctx["verification_code"] = code
        ctx["letter"] = letter
        ctx["is_valid"] = bool(
            letter and letter.status in {CommitteeLetter.STATUS_APPROVED, CommitteeLetter.STATUS_ISSUED}
        )
        return ctx


class ExportContributionsCSV(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    def test_func(self):
        return self.request.user.role in [
            "chairperson", "treasurer", "secretary", "admin", "committee"
        ]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="contributions.csv"'
        writer = csv.writer(response)
        writer.writerow(["Member", "Amount (Ksh)", "Month", "Created At"])

        for c in Contribution.objects.all().order_by("-month"):
            writer.writerow([
                c.member.username,
                float(c.amount),
                c.month.strftime("%b %Y"),
                c.created_at.strftime("%Y-%m-%d")
            ])
        return response
    
class ExportContributionsPDF(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    def test_func(self):
        return self.request.user.role in [
            "chairperson", "treasurer", "admin", "committee"
        ]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="Contributions_Report.pdf"'
        p = canvas.Canvas(response, pagesize=A4)
        width, height = A4

        # HEADER BRANDING
        logo_path = finders.find("images/logo.png")
        if logo_path:
            p.drawImage(ImageReader(logo_path), 50, height - 80, width=50, height=50)

        p.setFont("Helvetica-Bold", 16)
        p.drawString(120, height - 50, "Tambul Hustle Youth Group")
        p.setFont("Helvetica", 12)
        p.drawString(120, height - 70, "Contributions Report")
        p.line(40, height - 85, width - 40, height - 85)

        # METADATA
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(50, height - 100, f"Generated on: {date.today().strftime('%B %d, %Y')}")

        # TABLE HEADERS
        y = height - 130
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Member")
        p.drawString(200, y, "Amount")
        p.drawString(300, y, "Month")
        p.drawString(400, y, "Date Added")
        y -= 20

        # DATA ROWS
        p.setFont("Helvetica", 9)
        for c in Contribution.objects.all().order_by("-month"):
            p.drawString(50, y, c.member.username)
            p.drawString(200, y, f"{float(c.amount):,.2f}")
            p.drawString(300, y, c.month.strftime("%b %Y"))
            p.drawString(400, y, c.created_at.strftime("%Y-%m-%d"))
            y -= 15
            if y < 50:
                p.showPage()
                y = height - 130

        #FOOTER
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(200, 30, "© Tambul Hustle Youth Group")

        p.save()
        return response


class ExportWelfareCSV(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    def test_func(self):
        return self.request.user.role in [
            "chairperson", "welfare", "treasurer", "admin", "committee"
        ]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="welfare.csv"'
        writer = csv.writer(response)
        writer.writerow(["Member", "Amount (Ksh)", "Description", "Status", "Date Given"])

        for w in Welfare.objects.all().order_by("-date_given"):
            writer.writerow([
                w.member.username,
                float(w.amount),
                w.description,
                w.get_status_display(),
                w.date_given.strftime("%Y-%m-%d")
            ])
        return response

class ExportWelfarePDF(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    def test_func(self):
        return self.request.user.role in [
            "chairperson", "welfare", "treasurer", "admin", "committee"
        ]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="Welfare_Report.pdf"'
        p = canvas.Canvas(response, pagesize=A4)
        width, height = A4

        #HEADER BRANDING
        logo_path = finders.find("images/logo.png")
        if logo_path:
            p.drawImage(ImageReader(logo_path), 50, height - 80, width=50, height=50)

        p.setFont("Helvetica-Bold", 16)
        p.drawString(120, height - 50, "Tambul Hustle Youth Group")
        p.setFont("Helvetica", 12)
        p.drawString(120, height - 70, "Welfare Report")
        p.line(40, height - 85, width - 40, height - 85)

        #METADATA
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(50, height - 100, f"Generated on: {date.today().strftime('%B %d, %Y')}")

        # TABLE HEADERS
        y = height - 130
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Member")
        p.drawString(180, y, "Amount (Ksh)")
        p.drawString(280, y, "Status")
        p.drawString(360, y, "Description")
        p.drawString(500, y, "Date Given")
        y -= 20

        # DATA ROWS
        p.setFont("Helvetica", 9)
        for w in Welfare.objects.all().order_by("-date_given"):
            p.drawString(50, y, w.member.username)
            p.drawString(180, y, f"{float(w.amount):,.2f}")
            p.drawString(280, y, w.get_status_display())
            desc = (w.description[:30] + "...") if len(w.description) > 30 else w.description
            p.drawString(360, y, desc)
            p.drawString(500, y, w.date_given.strftime("%Y-%m-%d"))
            y -= 15
            if y < 50:
                p.showPage()
                y = height - 130

        #FOOTER
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(200, 30, "© Tambul Hustle Youth Group")

        p.save()
        return response


class ExportLoansCSV(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    def test_func(self):
        return self.request.user.role in [
            "chairperson", "treasurer", "secretary", "admin", "committee"
        ]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="loans.csv"'
        writer = csv.writer(response)
        writer.writerow([
            "Member",
            "Amount (Ksh)",
            "Total Paid (Ksh)",
            "Balance (Ksh)",
            "Approval Status",
            "Loan Date",
            "Due Date",
            "Repayment Status",
            "Updated On",
        ])

        for loan in Loan.objects.all().order_by("-created_at"):
            member_name = loan.member.get_full_name() or loan.member.username
            writer.writerow([
                member_name,
                float(loan.amount),
                float(loan.total_paid_so_far),
                float(loan.current_balance()),
                loan.get_status_display(),
                loan.loan_date.strftime("%Y-%m-%d") if loan.loan_date else "",
                loan.due_date.strftime("%Y-%m-%d") if loan.due_date else "",
                loan.get_repayment_status_display(),
                loan.repayment_updated_at.strftime("%Y-%m-%d") if loan.repayment_updated_at else "",
            ])
        return response

class ExportLoansPDF(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    def test_func(self):
        return self.request.user.role in [
            "chairperson", "treasurer", "secretary", "admin", "committee"
        ]

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="Loans_Report.pdf"'
        p = canvas.Canvas(response, pagesize=landscape(A4))
        width, height = landscape(A4)

        #HEADER BRANDING
        logo_path = finders.find("images/logo.png")
        if logo_path:
            p.drawImage(ImageReader(logo_path), 50, height - 80, width=50, height=50)

        p.setFont("Helvetica-Bold", 16)
        p.drawString(120, height - 50, "Tambul Hustle Youth Group")
        p.setFont("Helvetica", 12)
        p.drawString(120, height - 70, "Loan Report")
        p.line(40, height - 85, width - 40, height - 85)
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(50, height - 100, f"Generated on: {date.today().strftime('%B %d, %Y')}")

        # TABLE HEADERS
        y = height - 130
        p.setFont("Helvetica-Bold", 8)
        p.drawString(35, y, "Member")
        p.drawString(140, y, "Amount")
        p.drawString(205, y, "Total Paid")
        p.drawString(280, y, "Balance")
        p.drawString(350, y, "Approval")
        p.drawString(425, y, "Loan Date")
        p.drawString(500, y, "Due Date")
        p.drawString(575, y, "Repayment")
        p.drawString(670, y, "Updated On")
        y -= 20

        #DATA ROWS
        p.setFont("Helvetica", 8)
        for loan in Loan.objects.all().order_by("-created_at"):
            member_name = loan.member.get_full_name() or loan.member.username
            p.drawString(35, y, member_name[:22])
            p.drawString(140, y, f"{float(loan.amount):,.2f}")
            p.drawString(205, y, f"{float(loan.total_paid_so_far):,.2f}")
            p.drawString(280, y, f"{float(loan.current_balance()):,.2f}")
            p.drawString(350, y, loan.get_status_display())
            if loan.loan_date:
                p.drawString(425, y, loan.loan_date.strftime("%Y-%m-%d"))
            if loan.due_date:
                p.drawString(500, y, loan.due_date.strftime("%Y-%m-%d"))
            p.drawString(575, y, loan.get_repayment_status_display())
            if loan.repayment_updated_at:
                p.drawString(670, y, loan.repayment_updated_at.strftime("%Y-%m-%d"))
            y -= 15
            if y < 50:
                p.showPage()
                y = height - 130

        #FOOTER
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(200, 30, "© Tambul Hustle Youth Group")

        p.save()
        return response

class LoanRepaymentUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk, status):
        # Restrict to chairperson only
        if request.user.role != "chairperson":
            return HttpResponseForbidden("You are not authorized to update loan repayments.")

        loan = get_object_or_404(Loan, pk=pk)

        # Update repayment status and date
        valid_statuses = ["fully_paid", "partially_paid", "not_paid", "late"]
        if status in valid_statuses:
            loan.repayment_status = status
            loan.repayment_updated_at = timezone.now()
            loan.save()
            notify_users(
                recipients=[loan.member],
                title="Loan Repayment Status Updated",
                message=f"Your loan repayment status is now {loan.get_repayment_status_display()}.",
                link="/member-dashboard",
                send_email=True,
            )
            messages.success(request, f"Loan for {loan.member.username} marked as {status.replace('_', ' ').title()}.")
        else:
            messages.warning(request, "Invalid repayment status.")

        return redirect("committee-dashboard")

class LoanTotalPaidUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if request.user.role != "chairperson":
            return HttpResponseForbidden("You are not authorized to update loan repayments.")

        loan = get_object_or_404(Loan, pk=pk)
        raw_amount = request.POST.get("total_paid_so_far", "").strip()

        try:
            amount_repaid = Decimal(raw_amount)
        except (InvalidOperation, TypeError):
            messages.warning(request, "Enter a valid total paid amount.")
            return redirect("committee-dashboard")

        if amount_repaid < 0:
            messages.warning(request, "Total paid cannot be negative.")
            return redirect("committee-dashboard")

        total_balance = loan.total_balance
        if amount_repaid > total_balance:
            messages.warning(request, "Total paid cannot exceed the total balance.")
            return redirect("committee-dashboard")

        loan.total_paid_so_far = amount_repaid
        if amount_repaid == 0:
            loan.repayment_status = "not_paid"
        elif amount_repaid < total_balance:
            loan.repayment_status = "partially_paid"
        else:
            loan.repayment_status = "fully_paid"

        loan.repayment_updated_at = timezone.localdate()
        loan.save(update_fields=["total_paid_so_far", "repayment_status", "repayment_updated_at"])
        notify_users(
            recipients=[loan.member],
            title="Loan Payment Updated",
            message=(
                f"Your loan payment was updated. Total paid: Ksh {loan.total_paid_so_far}. "
                f"Current status: {loan.get_repayment_status_display()}."
            ),
            link="/member-dashboard",
            send_email=True,
        )
        messages.success(request, f"Total paid updated for {loan.member.username}.")
        return redirect("committee-dashboard")

class ContributionStatusUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk, status):
        # Restrict to Treasurer only
        if request.user.role != "treasurer":
            return HttpResponseForbidden("You are not authorized to update contributions.")

        contribution = get_object_or_404(Contribution, pk=pk)

        valid_statuses = ["fully_paid", "partially_paid", "late", "not_paid"]
        if status in valid_statuses:
            contribution.status = status
            contribution.updated_at = timezone.now()
            contribution.save()
            notify_users(
                recipients=[contribution.member],
                title="Contribution Status Updated",
                message=(
                    f"Your contribution for {contribution.month.strftime('%B %Y')} "
                    f"is now {contribution.get_status_display()}."
                ),
                link="/member-dashboard",
                send_email=True,
            )
            messages.success(
                request,
                f"Contribution for {contribution.member.username} marked as {status.title()}."
            )
        else:
            messages.warning(request, "Invalid contribution status.")

        return redirect("committee-dashboard")

class ContributionAmountUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if request.user.role != "treasurer":
            return HttpResponseForbidden("You are not authorized to update contributions.")

        contribution = get_object_or_404(Contribution, pk=pk)
        raw_amount = request.POST.get("amount", "").strip()

        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, TypeError):
            messages.warning(request, "Enter a valid contribution amount.")
            return redirect("committee-dashboard")

        if amount < 0:
            messages.warning(request, "Contribution amount cannot be negative.")
            return redirect("committee-dashboard")

        if amount >= Decimal("200"):
            derived_status = "fully_paid"
        elif amount > 0:
            derived_status = "partially_paid"
        else:
            derived_status = "not_paid"

        contribution.amount = amount
        contribution.status = derived_status
        contribution.updated_at = timezone.now()
        contribution.save(update_fields=["amount", "status", "updated_at"])
        notify_users(
            recipients=[contribution.member],
            title="Contribution Amount Updated",
            message=(
                f"Your contribution amount for {contribution.month.strftime('%B %Y')} "
                f"was updated to Ksh {contribution.amount}. "
                f"Current status: {contribution.get_status_display()}."
            ),
            link="/member-dashboard",
            send_email=True,
        )
        messages.success(request, f"Contribution amount updated for {contribution.member.username}.")
        return redirect("committee-dashboard")

class WelfareAmountUpdateView(LoginRequiredMixin, View):
    def post(self, request, pk):
        if request.user.role != "welfare":
            return HttpResponseForbidden("You are not authorized to update welfare records.")

        welfare = get_object_or_404(Welfare, pk=pk)
        raw_amount = request.POST.get("amount", "").strip()

        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, TypeError):
            messages.warning(request, "Enter a valid welfare amount.")
            return redirect("committee-dashboard")

        if amount < 0:
            messages.warning(request, "Welfare amount cannot be negative.")
            return redirect("committee-dashboard")

        welfare.amount = amount
        welfare.updated_at = timezone.now()
        welfare.save(update_fields=["amount", "updated_at"])
        notify_users(
            recipients=[welfare.member],
            title="Welfare Amount Updated",
            message=f"Your welfare amount was updated to Ksh {welfare.amount}.",
            link="/member-dashboard",
            send_email=True,
        )
        messages.success(request, f"Welfare amount updated for {welfare.member.username}.")
        return redirect("committee-dashboard")

class WelfareStatusUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk, status):
        # Restrict only to Welfare
        if request.user.role != "welfare":
            return HttpResponseForbidden("You are not authorized to update welfare records.")

        welfare = get_object_or_404(Welfare, pk=pk)

        valid_statuses = ["fully_paid", "partially_paid", "late", "not_paid"]
        if status in valid_statuses:
            welfare.status = status
            welfare.updated_at = timezone.now()
            welfare.save()
            notify_users(
                recipients=[welfare.member],
                title="Welfare Status Updated",
                message=f"Your welfare status is now {welfare.get_status_display()}.",
                link="/member-dashboard",
                send_email=True,
            )
            messages.success(
                request,
                f"Welfare record for {welfare.member.username} marked as {status.replace('_', ' ').title()}."
            )
        else:
            messages.warning(request, "Invalid welfare status.")

        return redirect("committee-dashboard")

class LoanApplicationView(LoginRequiredMixin, CreateView):
    model = Loan
    form_class = LoanApplicationForm
    template_name = "core/apply_loan.html"
    success_url = reverse_lazy("member-dashboard")
    committee_roles = COMMITTEE_ROLES

    def form_valid(self, form):
        user = self.request.user

        # Block duplicate unpaid or active loans
        existing_loan = Loan.objects.filter(
            member=user,
            repayment_status__in=["not_paid", "partially_paid", "late"],
            status__in=["pending", "approved"],
        ).exists()

        if existing_loan:
            messages.warning(
                self.request,
                "You already have an unpaid or active loan. Please clear it before applying again.",
            )
            return redirect(self.get_success_url())

        loan = form.save(commit=False)
        loan.member = user
        loan.status = "pending"
        loan.repayment_status = "not_paid"
        loan.save()
        notify_users(
            recipients=[user],
            title="Loan Application Submitted",
            message=f"Your loan application for Ksh {loan.amount} has been submitted and is pending review.",
            link="/member-dashboard",
            send_email=True,
        )
        committee_recipients = committee_users().exclude(pk=user.pk)
        notify_users(
            recipients=committee_recipients,
            title="New Loan Application",
            message=f"{user.get_full_name() or user.username} applied for a loan of Ksh {loan.amount}.",
            link="/committee-dashboard/",
            send_email=True,
        )

        messages.success(
            self.request, "Your loan application has been submitted successfully."
        )
        return super().form_valid(form)

    def get_success_url(self):
        user_role = getattr(self.request.user, "role", "")
        if user_role in self.committee_roles:
            return reverse("committee-dashboard")
        return reverse("member-dashboard")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user_role = getattr(self.request.user, "role", "")
        if user_role in self.committee_roles:
            ctx["cancel_url"] = reverse("committee-dashboard")
        else:
            ctx["cancel_url"] = reverse("member-dashboard")
        return ctx


class LoanApprovalUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk, status):
        if request.user.role != "chairperson":
            return HttpResponseForbidden("You are not authorized to update loan approval status.")

        loan = get_object_or_404(Loan, pk=pk)
        valid_statuses = {"approved", "rejected", "pending"}

        if status in valid_statuses:
            loan.status = status
            update_fields = ["status"]

            if status in {"pending", "rejected"}:
                loan.repayment_status = "not_paid"
                update_fields.append("repayment_status")

            loan.save(update_fields=update_fields)
            notify_users(
                recipients=[loan.member],
                title="Loan Application Status Updated",
                message=f"Your loan application status is now {loan.status.title()}.",
                link="/member-dashboard",
                send_email=True,
            )
            messages.success(
                request,
                f"Loan for {loan.member.username} marked as {status.replace('_', ' ').title()}."
            )
        else:
            messages.warning(request, "Invalid loan status.")

        return redirect("committee-dashboard")

class AnnouncementCreateView(LoginRequiredMixin, CreateView):
    model = Announcement
    form_class = AnnouncementForm
    template_name = "core/announcement.html"
    success_url = reverse_lazy("committee-dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.role != "coordinator":
            messages.error(request, "Only the Coordinator can create announcements.")
            return redirect("committee-dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Announcement posted successfully.")
        return super().form_valid(form)

class PostMeetingNoteView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = MeetingNote
    form_class = MeetingNoteForm
    template_name = "core/post_meeting.html"
    success_url = reverse_lazy("committee-dashboard")

    # only secretary can post
    def test_func(self):
        return self.request.user.role == "secretary"

    def form_valid(self, form):
        form.instance.posted_by = self.request.user
        response = super().form_valid(form)
        minute = self.object

        messages.success(self.request, "Meeting minutes posted successfully and notifications sent.")
        return response

class PostAnnouncementView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Announcement
    fields = ["title", "message"]
    template_name = "core/post_announcement.html"
    success_url = reverse_lazy("committee-dashboard")

    def test_func(self):
        return self.request.user.role == "coordinator"

    def form_valid(self, form):
        form.instance.published_at = timezone.now()
        form.instance.created_by = self.request.user
        messages.success(self.request, "Announcement posted successfully.")
        
        return super().form_valid(form)
    
class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = "core/profile.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["user"] = self.request.user
        return ctx
    
class EditProfileView(LoginRequiredMixin, UpdateView):
    model = User
    form_class=ProfileForm
    template_name = "core/edit_profile.html"
    success_url = reverse_lazy("profile")

    def get_object(self):
        return self.request.user

class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = "core/notifications.html"
    context_object_name = "notifications"

    def get_queryset(self):
        return self.request.user.notifications.all()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["unread_notifications"] = self.request.user.notifications.filter(is_read=False).count()
        return ctx

class MarkNotificationReadView(LoginRequiredMixin, View):
    def get(self, request, pk):
        notif = get_object_or_404(Notification, pk=pk, recipient=request.user)
        normalized_link = normalize_notification_link(notif.link)
        notif.is_read = True
        update_fields = ["is_read"]
        if normalized_link and normalized_link != notif.link:
            notif.link = normalized_link
            update_fields.append("link")
        notif.save(update_fields=update_fields)
        return redirect(normalized_link or "notifications")

class MarkAllNotificationsReadView(LoginRequiredMixin, View):
    def post(self, request):
        updated = request.user.notifications.filter(is_read=False).update(is_read=True)

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "updated": updated,
                "unread_count": 0,
            })

        return redirect(request.META.get("HTTP_REFERER") or reverse("notifications"))

class NotificationFetchView(LoginRequiredMixin, View):
    PAGE_SIZE = 10

    def get(self, request):
        page_number = request.GET.get("page", 1)
        user = request.user

        notifications_qs = user.notifications.order_by("-created_at")

        paginator = Paginator(notifications_qs, self.PAGE_SIZE)
        page_obj = paginator.get_page(page_number)
        unread_count = notifications_qs.filter(is_read=False).count()

        data = [
            {
                "id": n.id,
                "title": n.title or "Notification",
                "message": n.message or "",
                "link": normalize_notification_link(n.link) or "#",
                "is_read": n.is_read,
                "created_at": n.created_at.strftime("%b %d, %I:%M %p"),
            }
            for n in page_obj
        ]
        return JsonResponse({
            "notifications": data,
            "has_next": page_obj.has_next(),
            "next_page": page_obj.next_page_number() if page_obj.has_next() else None,
            "unread_count": unread_count,
        })

class AnnouncementDetailView(DetailView):
    model = Announcement
    template_name = "core/announcement_detail.html"
    context_object_name = "announcement"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        if user.is_authenticated:
            if user.role == "member":
                ctx["dashboard_url"] = "member-dashboard"
            else:
                ctx["dashboard_url"] = "committee-dashboard"
        else:
            ctx["dashboard_url"] = "index"
        return ctx

class MeetingMinutesDetailView(LoginRequiredMixin, View):
    def get(self, request, pk):
        note = get_object_or_404(MeetingNote, pk=pk)
        if (
            note.audience == MeetingNote.AUDIENCE_COMMITTEE
            and request.user.role not in COMMITTEE_ROLES
        ):
            return HttpResponseForbidden("You do not have permission to view this meeting note.")

        dashboard_url = (
            "member-dashboard" if request.user.role == "member" else "committee-dashboard"
        )
        has_file = (
            note.file
            and hasattr(note.file, "url")
            and note.file.name not in [None, "", "None"]
        )

        return render(
            request,
            "core/meeting_minutes_detail.html",
            {
                "note": note,
                "has_file": has_file,
                "dashboard_url": dashboard_url,
            },
        )

class ContributionUpdateView(PermissionRequiredMixin, UpdateView):
    model = Contribution
    fields = ["amount", "status"]
    permission_required = "core.edit_contribution_amount"

class WelfareUpdateView(PermissionRequiredMixin, UpdateView):
    model = Welfare
    fields = ["amount", "status", "description"]
    permission_required = "core.edit_welfare_amount"

class LoanUpdateView(PermissionRequiredMixin, UpdateView):
    model = Loan
    fields = ["amount", "repayment_status", "status"]
    permission_required = "core.edit_loan_amount"
