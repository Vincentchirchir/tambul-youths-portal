import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from accounts.models import User
from core.models import Contribution, Loan, MeetingNote, Welfare
from core.services.ai_tools import AI_TOOL_REGISTRY, get_ai_tool_schema, invoke_ai_tool
from core.services.ai_tools.constants import (
    AUTHENTICATION_REQUIRED,
    AUTHORIZATION_ALLOWED,
    AUTHORIZATION_DENIED,
    AUTHORIZATION_NOT_AUTHENTICATED,
    INVALID_ARGUMENT,
    PERMISSION_DENIED,
    SENSITIVITY_COMMITTEE_PRIVATE,
    SENSITIVITY_FINANCIAL_AGGREGATE,
    SENSITIVITY_FINANCIAL_PRIVATE,
    SENSITIVITY_LOW,
    UNSUPPORTED_REQUEST,
)
from payments.models import KcbPaymentNotification, PaymentIntent


class AIToolLayerTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="ai_member",
            password="pass1234",
            first_name="AI",
            last_name="Member",
            email="ai-member@example.com",
            phone="0700000000",
            national_id="12345678",
            role="member",
        )
        self.other_member = User.objects.create_user(
            username="ai_other",
            password="pass1234",
            first_name="Other",
            last_name="Member",
            role="member",
        )
        self.treasurer = User.objects.create_user(
            username="ai_treasurer",
            password="pass1234",
            role="treasurer",
        )
        self.chairperson = User.objects.create_user(
            username="ai_chairperson",
            password="pass1234",
            role="chairperson",
        )
        self.committee = User.objects.create_user(
            username="ai_committee",
            password="pass1234",
            role="committee",
        )
        self.welfare_officer = User.objects.create_user(
            username="ai_welfare",
            password="pass1234",
            role="welfare",
        )
        self.vice_chairperson = User.objects.create_user(
            username="ai_vice_chairperson",
            password="pass1234",
            role="vice-chairperson",
        )
        self.secretary = User.objects.create_user(
            username="ai_secretary",
            password="pass1234",
            role="secretary",
        )
        self.vice_secretary = User.objects.create_user(
            username="ai_vice_secretary",
            password="pass1234",
            role="vice-secretary",
        )
        self.coordinator = User.objects.create_user(
            username="ai_coordinator",
            password="pass1234",
            role="coordinator",
        )
        self.admin = User.objects.create_user(
            username="ai_admin",
            password="pass1234",
            role="admin",
        )

        self.contribution = Contribution.objects.create(
            member=self.member,
            amount=Decimal("100.00"),
            month=date(2026, 7, 1),
            status="partially_paid",
        )
        Contribution.objects.create(
            member=self.other_member,
            amount=Decimal("250.00"),
            month=date(2026, 7, 1),
            status="fully_paid",
        )
        self.loan = Loan.objects.create(
            member=self.member,
            amount=Decimal("1000.00"),
            total_paid_so_far=Decimal("200.00"),
            status="approved",
            due_date=date(2099, 1, 1),
        )
        Loan.objects.filter(pk=self.loan.pk).update(loan_date=date(2026, 7, 6))
        self.loan.refresh_from_db()
        self.other_loan = Loan.objects.create(
            member=self.other_member,
            amount=Decimal("9000.00"),
            total_paid_so_far=Decimal("0.00"),
            status="approved",
            due_date=date(2099, 1, 1),
        )
        Loan.objects.filter(pk=self.other_loan.pk).update(loan_date=date(2026, 7, 8))
        self.other_loan.refresh_from_db()
        self.welfare = Welfare.objects.create(
            member=self.member,
            description="Private welfare note",
            amount=Decimal("500.00"),
            status="fully_paid",
        )
        Welfare.objects.filter(pk=self.welfare.pk).update(date_given=date(2026, 7, 5))
        self.welfare.refresh_from_db()

        self.general_minutes = MeetingNote.objects.create(
            title="General AI minutes",
            description="Visible to authenticated members.",
            content="General minutes content.",
            audience=MeetingNote.AUDIENCE_ALL,
        )
        self.committee_minutes = MeetingNote.objects.create(
            title="Committee AI minutes",
            description="Committee-only content.",
            content="Committee private content " * 100,
            audience=MeetingNote.AUDIENCE_COMMITTEE,
        )
        self.payment_intent = PaymentIntent.objects.create(
            member=self.member,
            payment_type="monthly_contribution",
            amount_expected=Decimal("250.00"),
            customer_reference="AI-REF-001",
            contribution_month=date(2026, 7, 1),
        )
        KcbPaymentNotification.objects.create(
            payment_intent=self.payment_intent,
            transaction_reference="AI-TXN-001",
            request_id="REQ-AI-TXN-001",
            customer_reference="AI-REF-001",
            customer_name="AI Member",
            customer_mobile_number="254700000000",
            transaction_amount=Decimal("250.00"),
            raw_payload={
                "customerMobileNumber": "254700000000",
                "secret": "callback-secret",
            },
        )

    def assertNoSensitiveKeys(self, value):
        blocked = {
            "email",
            "phone",
            "national_id",
            "password",
            "raw_payload",
            "customer_reference",
            "transaction_reference",
            "customer_mobile_number",
            "member_id",
            "contribution_id",
            "posted_by_id",
        }
        if isinstance(value, dict):
            self.assertTrue(blocked.isdisjoint(value))
            for child in value.values():
                self.assertNoSensitiveKeys(child)
        elif isinstance(value, list):
            for item in value:
                self.assertNoSensitiveKeys(item)

    def test_registry_contains_only_approved_read_only_tools(self):
        expected_tools = {
            "get_current_user_context",
            "get_my_contribution_status",
            "get_my_loan_summary",
            "get_monthly_contribution_summary",
            "get_contribution_summary_range",
            "get_contribution_trends",
            "get_outstanding_contributions",
            "get_member_financial_snapshot",
            "get_loan_summary",
            "get_welfare_summary",
            "get_meeting_minutes",
            "get_financial_summary",
            "draft_member_reminder_context",
        }
        dangerous_names = {
            "save",
            "delete",
            "update",
            "create",
            "bulk_create",
            "bulk_update",
            "post_successful_payment",
            "post_monthly_contribution",
            "post_loan_repayment",
            "post_welfare_contribution",
            "approve_letter",
            "issue_letter",
        }

        self.assertEqual(set(AI_TOOL_REGISTRY), expected_tools)
        for name, definition in AI_TOOL_REGISTRY.items():
            self.assertFalse(definition.write_capable)
            self.assertTrue(definition.handler.__module__.endswith(".ai_tools.tools"))
            self.assertTrue(
                dangerous_names.isdisjoint({name, definition.handler.__name__})
            )
            self.assertIn("returns", get_ai_tool_schema(name))

        response = invoke_ai_tool(
            "query_database",
            self.treasurer,
            {"sql": "SELECT 1"},
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], UNSUPPORTED_REQUEST)

    def test_anonymous_user_cannot_invoke_protected_tools(self):
        response = invoke_ai_tool(
            "get_my_contribution_status",
            AnonymousUser(),
            {"year": 2026, "month": 7},
        )

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], AUTHENTICATION_REQUIRED)
        self.assertEqual(
            response["meta"]["audit"]["authorization_result"],
            AUTHORIZATION_NOT_AUTHENTICATED,
        )

    def test_member_self_tools_do_not_accept_another_member_identity(self):
        manipulated = invoke_ai_tool(
            "get_my_contribution_status",
            self.member,
            {"year": 2026, "month": 7, "member_id": self.other_member.pk},
        )
        response = invoke_ai_tool(
            "get_my_contribution_status",
            self.member,
            {"year": 2026, "month": 7},
        )
        loan_response = invoke_ai_tool("get_my_loan_summary", self.member, {})

        self.assertFalse(manipulated["ok"])
        self.assertEqual(manipulated["error"]["code"], INVALID_ARGUMENT)
        self.assertTrue(response["ok"])
        self.assertEqual(response["data"]["records"][0]["amount"], "100.00")
        self.assertNotIn("member_id", response["data"])
        self.assertNotIn("Other Member", json.dumps(response["data"]))
        self.assertTrue(loan_response["ok"])
        self.assertNotIn("9000.00", json.dumps(loan_response["data"]))

    def test_role_permissions_for_group_tools_follow_central_policy(self):
        denied = invoke_ai_tool(
            "get_monthly_contribution_summary",
            self.member,
            {"year": 2026, "month": 7},
        )
        treasurer_response = invoke_ai_tool(
            "get_monthly_contribution_summary",
            self.treasurer,
            {"year": 2026, "month": 7},
        )
        chairperson_response = invoke_ai_tool(
            "get_loan_summary",
            self.chairperson,
            {"year": 2026, "month": 7},
        )
        admin_response = invoke_ai_tool(
            "get_financial_summary",
            self.admin,
            {"year": 2026, "month": 7},
        )
        welfare_denied = invoke_ai_tool(
            "get_welfare_summary",
            self.member,
            {"year": 2026, "month": 7},
        )
        welfare_allowed = invoke_ai_tool(
            "get_welfare_summary",
            self.welfare_officer,
            {"year": 2026, "month": 7},
        )

        self.assertFalse(denied["ok"])
        self.assertEqual(denied["error"]["code"], PERMISSION_DENIED)
        self.assertTrue(treasurer_response["ok"])
        self.assertEqual(
            treasurer_response["meta"]["sensitivity"],
            SENSITIVITY_FINANCIAL_AGGREGATE,
        )
        self.assertTrue(chairperson_response["ok"])
        self.assertTrue(admin_response["ok"])
        self.assertFalse(welfare_denied["ok"])
        self.assertEqual(welfare_denied["error"]["code"], PERMISSION_DENIED)
        self.assertTrue(welfare_allowed["ok"])

    def test_all_committee_roles_can_use_broad_read_only_business_tools(self):
        committee_users = (
            self.admin,
            self.chairperson,
            self.vice_chairperson,
            self.committee,
            self.treasurer,
            self.secretary,
            self.vice_secretary,
            self.coordinator,
            self.welfare_officer,
        )
        tool_calls = (
            ("get_monthly_contribution_summary", {"year": 2026, "month": 7}),
            ("get_contribution_summary_range", {
                "start_year": 2026,
                "start_month": 7,
                "end_year": 2026,
                "end_month": 7,
            }),
            ("get_contribution_trends", {
                "start_year": 2026,
                "start_month": 7,
                "end_year": 2026,
                "end_month": 7,
            }),
            ("get_outstanding_contributions", {"year": 2026, "month": 7}),
            ("get_member_financial_snapshot", {"member_query": "AI Member"}),
            ("get_loan_summary", {"year": 2026, "month": 7}),
            ("get_welfare_summary", {"year": 2026, "month": 7}),
            ("get_financial_summary", {"year": 2026, "month": 7}),
            ("get_meeting_minutes", {"audience": MeetingNote.AUDIENCE_COMMITTEE}),
        )

        for user in committee_users:
            for tool_name, arguments in tool_calls:
                with self.subTest(role=user.role, tool=tool_name):
                    response = invoke_ai_tool(tool_name, user, arguments)
                    self.assertTrue(response["ok"], response.get("error"))

    def test_member_named_and_group_business_tools_remain_denied(self):
        denied_calls = (
            ("get_member_financial_snapshot", {"member_query": "Other Member"}),
            ("get_outstanding_contributions", {"year": 2026, "month": 7}),
            ("get_contribution_summary_range", {
                "start_year": 2026,
                "start_month": 7,
                "end_year": 2026,
                "end_month": 7,
            }),
            ("get_contribution_trends", {
                "start_year": 2026,
                "start_month": 7,
                "end_year": 2026,
                "end_month": 7,
            }),
            ("get_financial_summary", {"year": 2026, "month": 7}),
        )

        for tool_name, arguments in denied_calls:
            with self.subTest(tool=tool_name):
                response = invoke_ai_tool(tool_name, self.member, arguments)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], PERMISSION_DENIED)

    def test_contribution_range_trends_and_member_lookup_are_deterministic(self):
        Contribution.objects.create(
            member=self.member,
            amount=Decimal("250.00"),
            month=date(2026, 1, 1),
            status="fully_paid",
        )
        Contribution.objects.create(
            member=self.other_member,
            amount=Decimal("150.00"),
            month=date(2026, 2, 1),
            status="partially_paid",
        )

        range_response = invoke_ai_tool(
            "get_contribution_summary_range",
            self.committee,
            {
                "start_year": 2026,
                "start_month": 1,
                "end_year": 2026,
                "end_month": 2,
            },
        )
        trend_response = invoke_ai_tool(
            "get_contribution_trends",
            self.committee,
            {
                "start_year": 2026,
                "start_month": 1,
                "end_year": 2026,
                "end_month": 2,
            },
        )
        member_response = invoke_ai_tool(
            "get_member_financial_snapshot",
            self.committee,
            {"member_query": "ai member", "year": 2026, "month": 7},
        )
        missing_response = invoke_ai_tool(
            "get_member_financial_snapshot",
            self.committee,
            {"member_query": "Not A Member"},
        )
        User.objects.create_user(
            username="ai_member_duplicate",
            password="pass1234",
            first_name="AI",
            last_name="Duplicate",
            role="member",
        )
        ambiguous_response = invoke_ai_tool(
            "get_member_financial_snapshot",
            self.committee,
            {"member_query": "AI"},
        )

        self.assertTrue(range_response["ok"])
        self.assertEqual(range_response["data"]["period_count"], 2)
        self.assertEqual(range_response["data"]["collected_amount"], "400.00")
        self.assertTrue(trend_response["ok"])
        self.assertEqual(len(trend_response["data"]["periods"]), 2)
        self.assertEqual(
            trend_response["data"]["periods"][0]["total_collected"],
            "250.00",
        )
        self.assertTrue(member_response["ok"])
        self.assertEqual(member_response["data"]["resolution_status"], "resolved")
        self.assertEqual(
            member_response["data"]["contributions"]["total_paid"],
            "100.00",
        )
        self.assertEqual(
            member_response["data"]["loans"]["outstanding_balance"],
            "900.00",
        )
        self.assertNoSensitiveKeys(member_response["data"])
        self.assertEqual(missing_response["data"]["resolution_status"], "not_found")
        self.assertEqual(ambiguous_response["data"]["resolution_status"], "ambiguous")
        self.assertNotIn("Duplicate", json.dumps(ambiguous_response["data"]))

    def test_meeting_audience_restrictions_are_enforced_server_side(self):
        member_default = invoke_ai_tool("get_meeting_minutes", self.member, {})
        member_committee = invoke_ai_tool(
            "get_meeting_minutes",
            self.member,
            {"audience": MeetingNote.AUDIENCE_COMMITTEE},
        )
        committee_response = invoke_ai_tool(
            "get_meeting_minutes",
            self.committee,
            {"audience": MeetingNote.AUDIENCE_COMMITTEE, "limit": 1},
        )

        self.assertTrue(member_default["ok"])
        self.assertEqual(
            [minute["title"] for minute in member_default["data"]["minutes"]],
            [self.general_minutes.title],
        )
        self.assertFalse(member_committee["ok"])
        self.assertEqual(member_committee["error"]["code"], PERMISSION_DENIED)
        self.assertTrue(committee_response["ok"])
        self.assertEqual(
            committee_response["data"]["minutes"][0]["title"],
            self.committee_minutes.title,
        )
        self.assertLessEqual(
            len(committee_response["data"]["minutes"][0]["content_excerpt"]),
            503,
        )

    def test_invalid_arguments_are_rejected(self):
        invalid_month = invoke_ai_tool(
            "get_my_contribution_status",
            self.member,
            {"year": 2026, "month": 13},
        )
        invalid_year = invoke_ai_tool(
            "get_monthly_contribution_summary",
            self.treasurer,
            {"year": 1900, "month": 7},
        )
        invalid_status = invoke_ai_tool(
            "get_loan_summary",
            self.treasurer,
            {"status": "paid"},
        )
        long_query = invoke_ai_tool(
            "get_meeting_minutes",
            self.committee,
            {"query": "x" * 121},
        )

        self.assertEqual(invalid_month["error"]["code"], INVALID_ARGUMENT)
        self.assertEqual(invalid_year["error"]["code"], INVALID_ARGUMENT)
        self.assertEqual(invalid_status["error"]["code"], INVALID_ARGUMENT)
        self.assertEqual(long_query["error"]["code"], INVALID_ARGUMENT)

    def test_tool_outputs_are_allowlisted_and_exclude_sensitive_fields(self):
        responses = [
            invoke_ai_tool("get_current_user_context", self.member, {}),
            invoke_ai_tool(
                "get_my_contribution_status",
                self.member,
                {"year": 2026, "month": 7},
            ),
            invoke_ai_tool("get_my_loan_summary", self.member, {}),
            invoke_ai_tool(
                "get_outstanding_contributions",
                self.treasurer,
                {"year": 2026, "month": 7},
            ),
            invoke_ai_tool(
                "get_contribution_summary_range",
                self.treasurer,
                {
                    "start_year": 2026,
                    "start_month": 7,
                    "end_year": 2026,
                    "end_month": 7,
                },
            ),
            invoke_ai_tool(
                "get_contribution_trends",
                self.treasurer,
                {
                    "start_year": 2026,
                    "start_month": 7,
                    "end_year": 2026,
                    "end_month": 7,
                },
            ),
            invoke_ai_tool(
                "get_member_financial_snapshot",
                self.treasurer,
                {"member_query": "AI Member"},
            ),
            invoke_ai_tool(
                "get_meeting_minutes",
                self.committee,
                {"audience": MeetingNote.AUDIENCE_COMMITTEE},
            ),
            invoke_ai_tool(
                "draft_member_reminder_context",
                self.treasurer,
                {"year": 2026, "month": 7},
            ),
        ]

        for response in responses:
            self.assertTrue(response["ok"])
            self.assertNoSensitiveKeys(response["data"])
            self.assertNotIn("callback-secret", json.dumps(response["data"]))

    def test_tool_calls_do_not_modify_database_records(self):
        before = {
            "contributions": Contribution.objects.count(),
            "loans": Loan.objects.count(),
            "welfare": Welfare.objects.count(),
            "payments": PaymentIntent.objects.count(),
            "kcb_notifications": KcbPaymentNotification.objects.count(),
            "contribution_status": self.contribution.status,
            "loan_paid": self.loan.total_paid_so_far,
            "welfare_status": self.welfare.status,
            "payment_status": self.payment_intent.status,
        }

        calls = [
            ("get_current_user_context", self.member, {}),
            ("get_my_contribution_status", self.member, {"year": 2026, "month": 7}),
            ("get_my_loan_summary", self.member, {}),
            (
                "get_monthly_contribution_summary",
                self.treasurer,
                {"year": 2026, "month": 7},
            ),
            (
                "get_outstanding_contributions",
                self.treasurer,
                {"year": 2026, "month": 7},
            ),
            (
                "get_contribution_summary_range",
                self.treasurer,
                {
                    "start_year": 2026,
                    "start_month": 7,
                    "end_year": 2026,
                    "end_month": 7,
                },
            ),
            (
                "get_contribution_trends",
                self.treasurer,
                {
                    "start_year": 2026,
                    "start_month": 7,
                    "end_year": 2026,
                    "end_month": 7,
                },
            ),
            (
                "get_member_financial_snapshot",
                self.treasurer,
                {"member_query": "AI Member"},
            ),
            ("get_loan_summary", self.committee, {"year": 2026, "month": 7}),
            (
                "get_welfare_summary",
                self.welfare_officer,
                {"year": 2026, "month": 7},
            ),
            ("get_meeting_minutes", self.committee, {"query": "AI"}),
            ("get_financial_summary", self.chairperson, {"year": 2026, "month": 7}),
            (
                "draft_member_reminder_context",
                self.treasurer,
                {"year": 2026, "month": 7},
            ),
        ]

        for name, user, arguments in calls:
            self.assertTrue(invoke_ai_tool(name, user, arguments)["ok"])

        self.contribution.refresh_from_db()
        self.loan.refresh_from_db()
        self.welfare.refresh_from_db()
        self.payment_intent.refresh_from_db()
        self.assertEqual(Contribution.objects.count(), before["contributions"])
        self.assertEqual(Loan.objects.count(), before["loans"])
        self.assertEqual(Welfare.objects.count(), before["welfare"])
        self.assertEqual(PaymentIntent.objects.count(), before["payments"])
        self.assertEqual(
            KcbPaymentNotification.objects.count(),
            before["kcb_notifications"],
        )
        self.assertEqual(self.contribution.status, before["contribution_status"])
        self.assertEqual(self.loan.total_paid_so_far, before["loan_paid"])
        self.assertEqual(self.welfare.status, before["welfare_status"])
        self.assertEqual(self.payment_intent.status, before["payment_status"])

    def test_audit_event_is_recorded_without_response_body(self):
        with patch("core.services.ai_tools.registry.record_tool_audit_event") as record:
            response = invoke_ai_tool(
                "get_financial_summary",
                self.treasurer,
                {"year": 2026, "month": 7},
            )

        self.assertTrue(response["ok"])
        event = record.call_args.args[0]
        self.assertEqual(event["user_id"], self.treasurer.id)
        self.assertEqual(event["user_role"], "treasurer")
        self.assertEqual(event["tool_name"], "get_financial_summary")
        self.assertEqual(event["authorization_result"], AUTHORIZATION_ALLOWED)
        self.assertEqual(event["sensitivity"], SENSITIVITY_FINANCIAL_AGGREGATE)
        self.assertTrue(event["sensitive_data_accessed"])
        self.assertTrue(event["success"])
        self.assertNotIn("data", event)
        self.assertNotIn("response", event)
        self.assertNotIn("prompt", event)
        self.assertNotIn("100.00", json.dumps(event))

    def test_permission_denials_are_auditable(self):
        with patch("core.services.ai_tools.registry.record_tool_audit_event") as record:
            response = invoke_ai_tool(
                "get_financial_summary",
                self.member,
                {"year": 2026, "month": 7},
            )

        self.assertFalse(response["ok"])
        event = record.call_args.args[0]
        self.assertEqual(event["authorization_result"], AUTHORIZATION_DENIED)
        self.assertEqual(event["error_category"], PERMISSION_DENIED)
        self.assertFalse(event["success"])

    def test_sensitivity_classifications_are_explicit(self):
        expected = {
            "get_current_user_context": SENSITIVITY_LOW,
            "get_my_contribution_status": SENSITIVITY_FINANCIAL_PRIVATE,
            "get_my_loan_summary": SENSITIVITY_FINANCIAL_PRIVATE,
            "get_monthly_contribution_summary": SENSITIVITY_FINANCIAL_AGGREGATE,
            "get_contribution_summary_range": SENSITIVITY_FINANCIAL_AGGREGATE,
            "get_contribution_trends": SENSITIVITY_FINANCIAL_AGGREGATE,
            "get_outstanding_contributions": SENSITIVITY_FINANCIAL_PRIVATE,
            "get_member_financial_snapshot": SENSITIVITY_FINANCIAL_PRIVATE,
            "get_loan_summary": SENSITIVITY_FINANCIAL_AGGREGATE,
            "get_welfare_summary": SENSITIVITY_FINANCIAL_AGGREGATE,
            "get_meeting_minutes": SENSITIVITY_COMMITTEE_PRIVATE,
            "get_financial_summary": SENSITIVITY_FINANCIAL_AGGREGATE,
            "draft_member_reminder_context": SENSITIVITY_FINANCIAL_PRIVATE,
        }

        self.assertEqual(
            {
                name: definition.sensitivity
                for name, definition in AI_TOOL_REGISTRY.items()
            },
            expected,
        )
