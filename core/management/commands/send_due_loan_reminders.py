from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Contribution, Loan, LoanReminderLog, Notification, Welfare
from core.services.notifications import notify_users


class Command(BaseCommand):
    help = (
        "Send member reminders for due/overdue loans, monthly contributions, "
        "and welfare balances."
    )

    due_soon_days = (7, 3, 1)

    def add_arguments(self, parser):
        parser.add_argument(
            "--backfill-days",
            type=int,
            default=0,
            help=(
                "Re-process reminders for previous days (for catch-up after downtime). "
                "Example: --backfill-days 3 processes 3 days ago up to today."
            ),
        )

    def handle(self, *args, **options):
        backfill_days = max(0, int(options.get("backfill_days", 0)))
        today = timezone.now().date()
        total_sent = 0
        loan_sent = 0
        contribution_sent = 0
        welfare_sent = 0

        for days_ago in range(backfill_days, -1, -1):
            reference_date = today - timedelta(days=days_ago)
            loan_sent += self._process_loans(reference_date)

        # Monthly contribution reminders are sent only before/on day 10.
        if today.day <= 10:
            contribution_sent = self._send_contribution_reminders(today)

        welfare_sent = self._send_welfare_reminders(today)
        total_sent = loan_sent + contribution_sent + welfare_sent

        self.stdout.write(
            self.style.SUCCESS(
                "Reminder job completed. "
                f"Loan: {loan_sent}, Contributions: {contribution_sent}, "
                f"Welfare: {welfare_sent}, Total: {total_sent}"
            )
        )

    def _process_loans(self, reference_date):
        loans = Loan.objects.filter(
            status="approved",
            repayment_status__in=["not_paid", "partially_paid"],
            due_date__isnull=False,
            member__is_active=True,
        ).select_related("member")

        sent = 0
        for loan in loans:
            days_until_due = (loan.due_date - reference_date).days
            if days_until_due in self.due_soon_days:
                sent += self._send_due_soon(loan, reference_date, days_until_due)
                continue

            if days_until_due < 0:
                overdue_days = abs(days_until_due)
                # Send overdue reminders daily for first 3 days, then every 3 days.
                if overdue_days <= 3 or overdue_days % 3 == 0:
                    sent += self._send_overdue(loan, reference_date, overdue_days)
        return sent

    def _send_due_soon(self, loan, today, days_until_due):
        exists = LoanReminderLog.objects.filter(
            loan=loan,
            reminder_type=LoanReminderLog.REMINDER_DUE_SOON,
            reminder_date=today,
        ).exists()
        if exists:
            return 0

        notify_users(
            recipients=[loan.member],
            title="Loan Due Soon",
            message=(
                f"Your loan is due in {days_until_due} day(s) on {loan.due_date}. "
                f"Please clear your balance to avoid penalties."
            ),
            link="/member-dashboard",
            send_email=True,
        )

        LoanReminderLog.objects.create(
            loan=loan,
            reminder_type=LoanReminderLog.REMINDER_DUE_SOON,
            reminder_date=today,
            days_offset=days_until_due,
        )
        return 1

    def _send_overdue(self, loan, today, overdue_days):
        exists = LoanReminderLog.objects.filter(
            loan=loan,
            reminder_type=LoanReminderLog.REMINDER_OVERDUE,
            reminder_date=today,
        ).exists()
        if exists:
            return 0

        notify_users(
            recipients=[loan.member],
            title="Loan Overdue Reminder",
            message=(
                f"Your loan has been overdue for {overdue_days} day(s). "
                "Please make payment as soon as possible."
            ),
            link="/member-dashboard",
            send_email=True,
        )

        LoanReminderLog.objects.create(
            loan=loan,
            reminder_type=LoanReminderLog.REMINDER_OVERDUE,
            reminder_date=today,
            days_offset=-overdue_days,
        )
        return 1

    def _send_contribution_reminders(self, today):
        month_start = today.replace(day=1)
        unpaid_statuses = ["not_paid", "partially_paid", "late"]
        contributions = (
            Contribution.objects.filter(
                member__is_active=True,
                status__in=unpaid_statuses,
                month__lte=month_start,
            )
            .select_related("member")
            .order_by("member_id", "month")
        )

        grouped = {}
        for contribution in contributions:
            grouped.setdefault(contribution.member_id, {"member": contribution.member, "months": []})
            grouped[contribution.member_id]["months"].append(contribution.month.strftime("%b %Y"))

        sent = 0
        for payload in grouped.values():
            member = payload["member"]
            if self._already_sent_today(member.pk, "Monthly Contribution Reminder", today):
                continue
            months = payload["months"][:5]
            suffix = "..." if len(payload["months"]) > 5 else ""
            notify_users(
                recipients=[member],
                title="Monthly Contribution Reminder",
                message=(
                    f"You have {len(payload['months'])} contribution record(s) not fully paid "
                    f"({', '.join(months)}{suffix}). Please settle before the 10th of this month."
                ),
                link="/member-dashboard",
                send_email=True,
            )
            sent += 1
        return sent

    def _send_welfare_reminders(self, today):
        unpaid_statuses = ["not_paid", "partially_paid", "late"]
        welfare_records = (
            Welfare.objects.filter(member__is_active=True, status__in=unpaid_statuses)
            .select_related("member")
            .order_by("member_id", "-date_given")
        )

        grouped = {}
        for welfare in welfare_records:
            grouped.setdefault(welfare.member_id, {"member": welfare.member, "count": 0})
            grouped[welfare.member_id]["count"] += 1

        sent = 0
        for payload in grouped.values():
            member = payload["member"]
            if self._already_sent_today(member.pk, "Welfare Payment Reminder", today):
                continue
            notify_users(
                recipients=[member],
                title="Welfare Payment Reminder",
                message=(
                    f"You have {payload['count']} welfare record(s) not fully paid. "
                    "Please settle your balance as soon as possible."
                ),
                link="/member-dashboard",
                send_email=True,
            )
            sent += 1
        return sent

    def _already_sent_today(self, user_id, title, today):
        return Notification.objects.filter(
            recipient_id=user_id,
            title=title,
            created_at__date=today,
        ).exists()
