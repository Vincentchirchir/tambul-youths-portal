from django.core.management.base import BaseCommand
from django.utils.timezone import now
from datetime import date
from core.models import Contribution
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = "Generate monthly contribution records for all members"

    def handle(self, *args, **options):
        today = now().date()
        month_start = date(today.year, today.month, 1)

        members = User.objects.filter(is_active=True)

        created_count = 0

        for member in members:
            obj, created = Contribution.objects.get_or_create(
                member=member,
                month=month_start,
                defaults={
                    "amount": 0,
                    "status": "not_paid"
                }
            )
            if created:
                created_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Created {created_count} contribution records for {month_start}")
        )
