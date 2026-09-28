from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Avg, Count, Sum
from django.utils import timezone

from core.models import AIAuditLog
from core.services.ai.errors import (
    AI_PILOT_ACCESS_DENIED,
    AI_PROVIDER_TIMEOUT,
    AI_PROVIDER_UNAVAILABLE,
    AI_RATE_LIMITED,
    AI_RATE_LIMIT_UNAVAILABLE,
)
from core.services.ai_tools.constants import PERMISSION_DENIED


class Command(BaseCommand):
    help = "Print metadata-only Tambul Assistant pilot usage from AIAuditLog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Number of recent days to include.",
        )
        parser.add_argument(
            "--role",
            default="",
            help="Optional user role filter.",
        )

    def handle(self, *args, **options):
        days = max(int(options["days"]), 1)
        since = timezone.now() - timedelta(days=days)
        logs = AIAuditLog.objects.filter(created_at__gte=since)
        role = options.get("role", "").strip()
        if role:
            logs = logs.filter(user_role=role)

        request_logs = logs.filter(event_type=AIAuditLog.EVENT_REQUEST)
        tool_logs = logs.filter(event_type=AIAuditLog.EVENT_TOOL)
        totals = request_logs.aggregate(
            request_count=Count("id"),
            input_tokens=Sum("input_tokens"),
            output_tokens=Sum("output_tokens"),
            total_tokens=Sum("total_tokens"),
            average_latency_ms=Avg("duration_ms"),
        )
        successful_requests = request_logs.filter(success=True).count()
        failed_requests = request_logs.filter(success=False).count()
        distinct_users = request_logs.exclude(user_id=None).values(
            "user_id"
        ).distinct().count()

        provider_errors = request_logs.filter(
            error_category__in=(AI_PROVIDER_TIMEOUT, AI_PROVIDER_UNAVAILABLE)
        ).count()
        rate_limit_events = request_logs.filter(
            error_category__in=(AI_RATE_LIMITED, AI_RATE_LIMIT_UNAVAILABLE)
        ).count()
        pilot_denials = request_logs.filter(
            error_category=AI_PILOT_ACCESS_DENIED
        ).count()
        permission_denials = tool_logs.filter(
            error_category=PERMISSION_DENIED
        ).count()

        self.stdout.write("Tambul Assistant Pilot Usage Report")
        self.stdout.write(f"Window: last {days} day(s)")
        self.stdout.write(f"Role filter: {role or 'all'}")
        self.stdout.write(f"Request events: {totals['request_count'] or 0}")
        self.stdout.write(f"Distinct users: {distinct_users}")
        self.stdout.write(f"Successful requests: {successful_requests}")
        self.stdout.write(f"Failed requests: {failed_requests}")
        self.stdout.write(f"Tool executions: {tool_logs.count()}")
        self.stdout.write(f"Permission-denied tool events: {permission_denials}")
        self.stdout.write(f"Pilot access denials: {pilot_denials}")
        self.stdout.write(f"Provider errors: {provider_errors}")
        self.stdout.write(f"Rate-limit events: {rate_limit_events}")
        self.stdout.write(f"Input tokens: {totals['input_tokens'] or 0}")
        self.stdout.write(f"Output tokens: {totals['output_tokens'] or 0}")
        self.stdout.write(f"Total tokens: {totals['total_tokens'] or 0}")
        average_latency = totals["average_latency_ms"]
        if average_latency is None:
            average_latency = 0
        self.stdout.write(f"Average latency ms: {round(average_latency, 2)}")

        self.stdout.write("Top tools:")
        top_tools = list(
            tool_logs.exclude(tool_name="")
            .values("tool_name")
            .annotate(count=Count("id"))
            .order_by("-count", "tool_name")[:10]
        )
        if not top_tools:
            self.stdout.write("- none")
        for item in top_tools:
            self.stdout.write(f"- {item['tool_name']}: {item['count']}")
