from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Loan, LoanReminderLog
from core.services.notifications import notify_users


class Command(BaseCommand):
    help = "Send due-soon and overdue loan reminders to members."

    due_soon_days = (7, 3, 1)

    def handle(self, *args, **options):
        today = timezone.now().date()
        loans = Loan.objects.filter(
            status="approved",
            repayment_status__in=["not_paid", "partially_paid"],
            due_date__isnull=False,
            member__is_active=True,
        ).select_related("member")

        sent = 0

        for loan in loans:
            days_until_due = (loan.due_date - today).days
            if days_until_due in self.due_soon_days:
                sent += self._send_due_soon(loan, today, days_until_due)
                continue

            if days_until_due < 0:
                overdue_days = abs(days_until_due)
                # Send overdue reminders daily for the first 3 days, then every 3 days.
                if overdue_days <= 3 or overdue_days % 3 == 0:
                    sent += self._send_overdue(loan, today, overdue_days)

        self.stdout.write(
            self.style.SUCCESS(f"Loan reminder job completed. Notifications sent: {sent}")
        )

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
