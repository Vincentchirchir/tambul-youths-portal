from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group

class Command(BaseCommand):
    help = "Seed default groups for Tambul Hustle Youth Group Portal"

    GROUPS = [
        "Chairperson",
        "Committee",
        "Treasurer",
        "Secretary",
        "Coordinator",
        "Welfare",
        "Member",
    ]

    def handle(self, *args, **options):
        for name in self.GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            if created:
                self.stdout.write(self.style.SUCCESS(f"✅ Created group: {name}"))
            else:
                self.stdout.write(f"ℹ Group already exists: {name}")
        self.stdout.write(self.style.SUCCESS("✅ All default groups have been set up."))