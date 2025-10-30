from django.views.generic import TemplateView, CreateView
from django.views.generic.edit import FormView
from django.urls import reverse_lazy, reverse
from datetime import date
from django.db.models import Sum, F, ExpressionWrapper, DecimalField, Case, When, Value, Count
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from.models import Contribution, Loan, Welfare, Announcement, MeetingNote
from .forms import ContributionForm, LoanApplicationForm, AnnouncementForm, MeetingNoteForm
from django.shortcuts import render, redirect, get_object_or_404
import requests
from django.conf import settings
from accounts.models import User
from django.db.models.functions import TruncMonth
import csv
from django .contrib import messages
from django.http import HttpResponse, HttpResponseForbidden
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from django.contrib.staticfiles import finders
from reportlab.lib.utils import ImageReader
import json
from django.utils import timezone
from django.views import View


class Index(TemplateView):
    template_name="core/index.html"

class MemberDashboardView(LoginRequiredMixin, TemplateView):
    template_name="core/member_dashboard.html"
    def get_context_data(self, **kwargs):
        ctx=super().get_context_data(**kwargs)
        user=self.request.user
        year=date.today().year

        ctx["contrib_ytd"]=(
            Contribution.objects.filter(member=user, month__year=year)
            .aggregate(total=Sum("amount"))["total"]or 0
        )


        ctx["active_loan_count"]=Loan.objects.filter(
            member=user, status__in=["pending", "approved"]
            ).count()


        ctx["outstanding_principal"]=(
            Loan.objects.filter(member=user, status__in=["pending", "approved"])
            .aggregate(total=Sum("amount"))["total"] or 0
        )
        active_loans=Loan.objects.filter(member=user, status__in=["pending", "approved"])
        ctx["loan_balance"] = sum(loan.current_balance() for loan in active_loans)
        ctx["loans"]=active_loans
        ctx["today"]=date.today()

        ctx["can_apply_loan"] = not Loan.objects.filter(
            member=user,
            repayment_status__in=["not_paid", "partially_paid"],
            status__in=["pending", "approved"],
        ).exists()

        ctx["loans"] = Loan.objects.filter(member=user).order_by("-created_at")
        ctx["today"] = date.today()


        ctx["recent_contributions"]=(
            Contribution.objects.filter(member=user)
            .order_by("-month", "-created_at")[:6]
        )


        ctx["recent_loans"]=(
            Loan.objects.filter(member=user).order_by("-created_at")[:5]
        )

        ctx["recent_welfare"] =(
            Welfare.objects.filter(member=user).order_by("-date_given")[:5]
        )

        ctx["latest_announcements"] = (
            Announcement.objects.all().order_by("-published_at")[:5]
            if Announcement.objects.exists() else []
        )

        ctx["meeting_notes"] = MeetingNote.objects.all().order_by("-created_at")


        ctx["this_year"]=year
        return ctx

class CommitteeDashboardView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "core/committee_dashboard.html"

    # --- Access Control ---
    def test_func(self):
        return self.request.user.role in [
            "chairperson",
            "treasurer",
            "secretary",
            "welfare",
            "coordinator",
            "admin",
            "committee",
        ]

    # --- Context Data ---
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year = date.today().year
        user = self.request.user
        ctx["user_display_name"] = user.first_name or user.username
        # ========== 1️ OVERVIEW METRICS ==========
        ctx["total_members"] = User.objects.exclude(role="admin").count()
        ctx["total_contributions"] = (
            Contribution.objects.filter(month__year=year)
            .aggregate(total=Sum("amount"))["total"]
            or 0
        )
        ctx["total_loans"] = Loan.objects.filter(status="approved").count()
        ctx["pending_loans"] = Loan.objects.filter(status="pending").count()
        ctx["total_welfare"] = (
            Welfare.objects.aggregate(total=Sum("amount"))["total"] or 0
        )

        # Personal summary metrics for the logged-in committee member
        ctx["my_contrib_ytd"] = (
            Contribution.objects.filter(member=user, month__year=year)
            .aggregate(total=Sum("amount"))["total"] or 0
        )

        personal_active_loans = Loan.objects.filter(
            member=user, status__in=["pending", "approved"]
        )

        ctx["my_active_loan_count"] = personal_active_loans.count()
        ctx["my_outstanding_principal"] = (
            personal_active_loans.aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["my_loan_balance"] = sum(
            loan.current_balance() for loan in personal_active_loans
            if loan.current_balance()
        )

                # --- Check if committee member can apply loan ---
        ctx["can_apply_loan"] = not Loan.objects.filter(
            member=user,
            repayment_status__in=["not_paid", "partially_paid"],
            status__in=["pending", "approved"],
        ).exists()

        ctx["my_loans"] = Loan.objects.filter(member=user).order_by("-created_at")
        ctx["my_contributions"] = (
            Contribution.objects.filter(member=user).order_by("-month", "-created_at")[:6]
        )
        ctx["my_welfare"] = (
            Welfare.objects.filter(member=user).order_by("-date_given")[:6]
        )
        ctx["my_welfare_total"] = (
            Welfare.objects.filter(member=user).aggregate(total=Sum("amount"))["total"] or 0
        )
        ctx["latest_announcements"] = (
            Announcement.objects.all().order_by("-published_at")[:5]
            if Announcement.objects.exists()
            else []
        )
        ctx["today"] = date.today()

        # ========== 2️ DATA TABLES ==========
        ctx["members"] = (
            User.objects.all()
            .exclude(role="admin")
            .order_by("first_name")
        )
        ctx["loans"] = Loan.objects.all().order_by("-created_at")
        ctx["contributions"] = Contribution.objects.all().order_by("-month", "-created_at")
        ctx["welfare_records"] = Welfare.objects.all().order_by("-date_given")

        # ========== 3️ ANNOUNCEMENTS ==========
        ctx["announcements"] = (
            Announcement.objects.all().order_by("-published_at")[:10]
            if Announcement.objects.exists()
            else []
        )

        # ========== 4️ ANALYTICS DATA (for charts & summaries) ==========
        # Monthly contribution trend
        monthly = (
            Contribution.objects.filter(month__year=year)
            .annotate(month_label=TruncMonth("month"))
            .values("month_label")
            .annotate(total=Sum("amount"))
            .order_by("month_label")
        )
        ctx["monthly_labels"] = [m["month_label"].strftime("%b") for m in monthly]
        ctx["monthly_values"] = [float(m["total"]) for m in monthly]

        # Loan distribution by status
        loan_stats = Loan.objects.values("status").annotate(count=Count("id"))
        ctx["loan_labels"] = [l["status"] for l in loan_stats]
        ctx["loan_counts"] = [l["count"] for l in loan_stats]

        # Welfare totals by status
        welfare_stats = Welfare.objects.values("status").annotate(total=Sum("amount"))
        ctx["welfare_labels"] = [w["status"] for w in welfare_stats]
        ctx["welfare_totals"] = [float(w["total"]) for w in welfare_stats]
        #Meeting Reports
        ctx["meeting_notes"] = MeetingNote.objects.all().order_by("-created_at")

        # ========== 5️ YEAR & DATE INFO ==========
        ctx["this_year"] = year
        ctx["today"] = date.today()

        # Top 5 contributors (for bar chart)
        top_contrib = (
            Contribution.objects.values("member__username")
            .annotate(total=Sum("amount"))
            .order_by("-total")[:5]
        )
        ctx["top_contributors"] = [c["member__username"] for c in top_contrib]
        ctx["top_contrib_values"] = [float(c["total"]) for c in top_contrib]

        # Pre-serialize chart data for front-end consumption.
        ctx["chart_datasets"] = {
            "monthlyLabels": ctx["monthly_labels"],
            "monthlyValues": ctx["monthly_values"],
            "loanLabels": ctx["loan_labels"],
            "loanCounts": ctx["loan_counts"],
            "welfareLabels": ctx["welfare_labels"],
            "welfareTotals": ctx["welfare_totals"],
            "topContributors": ctx["top_contributors"],
            "topContribValues": ctx["top_contrib_values"],
        }
        ctx["chart_datasets_json"] = json.dumps(ctx["chart_datasets"])


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

        # --- HEADER BRANDING ---
        logo_path = finders.find("images/logo.png")
        if logo_path:
            p.drawImage(ImageReader(logo_path), 50, height - 80, width=50, height=50)

        p.setFont("Helvetica-Bold", 16)
        p.drawString(120, height - 50, "Tambul Hustle Youth Group")
        p.setFont("Helvetica", 12)
        p.drawString(120, height - 70, "Contributions Report")
        p.line(40, height - 85, width - 40, height - 85)

        # --- METADATA ---
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(50, height - 100, f"Generated on: {date.today().strftime('%B %d, %Y')}")

        # --- TABLE HEADERS ---
        y = height - 130
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Member")
        p.drawString(200, y, "Amount")
        p.drawString(300, y, "Month")
        p.drawString(400, y, "Date Added")
        y -= 20

        # --- DATA ROWS ---
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

        # --- FOOTER ---
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
                w.status.capitalize(),
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

        # --- HEADER BRANDING ---
        logo_path = finders.find("images/logo.png")
        if logo_path:
            p.drawImage(ImageReader(logo_path), 50, height - 80, width=50, height=50)

        p.setFont("Helvetica-Bold", 16)
        p.drawString(120, height - 50, "Tambul Hustle Youth Group")
        p.setFont("Helvetica", 12)
        p.drawString(120, height - 70, "Welfare Report")
        p.line(40, height - 85, width - 40, height - 85)

        # --- METADATA ---
        p.setFont("Helvetica-Oblique", 8)
        p.drawString(50, height - 100, f"Generated on: {date.today().strftime('%B %d, %Y')}")

        # --- TABLE HEADERS ---
        y = height - 130
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Member")
        p.drawString(180, y, "Amount (Ksh)")
        p.drawString(280, y, "Status")
        p.drawString(360, y, "Description")
        p.drawString(500, y, "Date Given")
        y -= 20

        # --- DATA ROWS ---
        p.setFont("Helvetica", 9)
        for w in Welfare.objects.all().order_by("-date_given"):
            p.drawString(50, y, w.member.username)
            p.drawString(180, y, f"{float(w.amount):,.2f}")
            p.drawString(280, y, w.status.capitalize())
            # Limit description to fit within page
            desc = (w.description[:30] + "...") if len(w.description) > 30 else w.description
            p.drawString(360, y, desc)
            p.drawString(500, y, w.date_given.strftime("%Y-%m-%d"))
            y -= 15
            if y < 50:
                p.showPage()
                y = height - 130

        # --- FOOTER ---
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
        writer.writerow(["Member", "Amount (Ksh)", "Status", "Loan Date", "Due Date"])

        for loan in Loan.objects.all().order_by("-created_at"):
            writer.writerow([
                loan.member.username,
                float(loan.amount),
                loan.status.capitalize(),
                loan.loan_date.strftime("%Y-%m-%d") if loan.loan_date else "",
                loan.due_date.strftime("%Y-%m-%d") if loan.due_date else ""
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
        p = canvas.Canvas(response, pagesize=A4)
        width, height = A4

        # --- HEADER BRANDING ---
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

        # --- TABLE HEADERS ---
        y = height - 130
        p.setFont("Helvetica-Bold", 10)
        p.drawString(50, y, "Member")
        p.drawString(180, y, "Amount")
        p.drawString(260, y, "Status")
        p.drawString(340, y, "Loan Date")
        p.drawString(440, y, "Due Date")
        y -= 20

        # --- DATA ROWS ---
        p.setFont("Helvetica", 9)
        for loan in Loan.objects.all().order_by("-created_at"):
            p.drawString(50, y, loan.member.username)
            p.drawString(180, y, f"{float(loan.amount):,.2f}")
            p.drawString(260, y, loan.status.capitalize())
            if loan.loan_date:
                p.drawString(340, y, loan.loan_date.strftime("%Y-%m-%d"))
            if loan.due_date:
                p.drawString(440, y, loan.due_date.strftime("%Y-%m-%d"))
            y -= 15
            if y < 50:
                p.showPage()
                y = height - 130

        # --- FOOTER ---
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
        valid_statuses = ["fully_paid", "partially_paid", "not_paid"]
        if status in valid_statuses:
            loan.repayment_status = status
            loan.repayment_updated_at = timezone.now()
            loan.save()
            messages.success(request, f"Loan for {loan.member.username} marked as {status.replace('_', ' ').title()}.")
        else:
            messages.warning(request, "Invalid repayment status.")

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
            messages.success(
                request,
                f"Contribution for {contribution.member.username} marked as {status.title()}."
            )
        else:
            messages.warning(request, "Invalid contribution status.")

        return redirect("committee-dashboard")

class WelfareStatusUpdateView(LoginRequiredMixin, View):
    def get(self, request, pk, status):
        # Restrict only to Welfare Officer
        if request.user.role != "welfare":
            return HttpResponseForbidden("You are not authorized to update welfare records.")

        welfare = get_object_or_404(Welfare, pk=pk)

        valid_statuses = ["fully paid", "partially paid", "late", "not paid"]
        if status in valid_statuses:
            welfare.status = status
            welfare.updated_at = timezone.now()
            welfare.save()
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
    committee_roles = {
        "chairperson",
        "treasurer",
        "secretary",
        "welfare",
        "coordinator",
        "admin",
        "committee",
    }

    def form_valid(self, form):
        user = self.request.user

        # Block duplicate unpaid or active loans
        existing_loan = Loan.objects.filter(
            member=user,
            repayment_status__in=["not_paid", "partially_paid"],
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

    def test_func(self):
        return self.request.user.role == "secretary"

    def form_valid(self, form):
        form.instance.posted_by = self.request.user
        messages.success(self.request, "Meeting minutes posted successfully.")
        return super().form_valid(form)

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
