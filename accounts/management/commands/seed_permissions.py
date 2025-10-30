from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission

class Command(BaseCommand):
    help="Attach built-in and custom permissions to THYG groups"

    GROUP_PERMS={
        "Chairperson":{
            "core.view_contribution",
            "core.add_contribution",
            "core.change_contribution",
            "core.delete_contribution",
            "core.export_contributions",
            "core.view_loan",
            "core.add_loan",
            "core.change_loan",
            "core.delete_loan",
            "core.approve_loan",
            "core.reject_loan",
            "core.view_welfare",
            "core.add_welfare",
            "core.change_welfare",
        },
        "Treasurer":{
            "core.view_contribution",
            "core.add_contribution",
            "core.change_contribution",
            "core.export_contributions",
            "core.view_loan",
        },
        "Secretary":{
            "core.view_contribution",
            "core.view_loan",
            "core.make_notes",
        },
        "Coordinator":{
            "core.view_contribution",
            "core.view_loan",
            "core.make_announcement",
        },
        "Welfare":{
            "core.view_contribution",
            "core.view_loan",
            "core.view_welfare",
            "core.add_welfare",
            "core.change_welfare",
        },
        "Member":{
            "core.view_contribution",
            "core.view_loan",
        },
    }

    def handle(self, *args, **kwargs):
        total=0
        for group_name, labels in self.GROUP_PERMS.items():
            group,_=Group.objects.get_or_create(name=group_name)
            attached, missing=0,[]
            for label in labels:
                try:
                    app, code=label.split(".")
                    perm=Permission.objects.get(content_type__app_label=app, codename=code)
                    group.permissions.add(perm)
                    attached +=1
                    total+=1
                except(ValueError, Permission.DoesNotExist):
                    missing.append(label)

            self.stdout.write(self.style.SUCCESS(f"✅{group_name}: attached {attached}"))
            if missing:
                self.stdout.write(self.style.WARNING(
                    f" ⚠️ Missing: {', '.join(missing)}"
                    f"(Did you define Meta.Permissions and run makemigrations/migrate?)"
                ))
        self.stdout.write(self.style.SUCCESS(f"Done. Total Permissions attached: {total}"))