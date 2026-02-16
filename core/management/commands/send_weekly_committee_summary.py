from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from core.models import Contribution, Loan, Welfare
from core.services.notifications import committee_users, notify_users


class Command(BaseCommand):
    help = (
        "Send weekly committee summary notifications "
        "(new loans, overdue loans, unpaid contributions, unpaid welfare)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Summary window in days for new loans (default: 7).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print summary only without sending notifications.",
        )

    def handle(self, *args, **options):
        days = max(1, options["days"])
        dry_run = options["dry_run"]

        now = timezone.now()
        window_start = now - timedelta(days=days)
        today = now.date()

        new_loans_qs = Loan.objects.filter(created_at__gte=window_start)
        new_loans_count = new_loans_qs.count()
        new_loans_amount = new_loans_qs.aggregate(total=Sum("amount"))["total"] or 0

        overdue_loans_count = Loan.objects.filter(
            status="approved",
            repayment_status__in=["not_paid", "partially_paid"],
            due_date__lt=today,
        ).count()

        unpaid_contributions_count = Contribution.objects.filter(
            status__in=["not_paid", "partially_paid", "late"],
        ).count()

        unpaid_welfare_count = Welfare.objects.filter(
            status__in=["not_paid", "partially_paid", "late"],
        ).count()

        title = "Weekly Committee Summary"
        message = (
            f"Last {days} day(s): {new_loans_count} new loan(s) "
            f"(Ksh {new_loans_amount}). "
            f"Current overdue loans: {overdue_loans_count}. "
            f"Unpaid contributions: {unpaid_contributions_count}. "
            f"Unpaid welfare records: {unpaid_welfare_count}."
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run enabled. No notifications sent."))
            self.stdout.write(message)
            return

        recipients = committee_users()
        sent = notify_users(
            recipients=recipients,
            title=title,
            message=message,
            link="/committee-dashboard/",
            send_email=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Weekly committee summary sent to {sent} committee member(s)."
            )
        )
