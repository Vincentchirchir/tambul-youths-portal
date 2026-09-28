from datetime import date
from decimal import Decimal

from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.models import Contribution, Loan, Welfare
from core.services.ai.ui import ai_assistant_suggested_prompts, ai_assistant_ui_context
from payments.models import PaymentIntent


AI_UI_SETTINGS = {
    "AI_ENABLED": True,
    "OPENAI_API_KEY": "test-openai-key",
    "OPENAI_MODEL": "test-model",
    "AI_MAX_INPUT_LENGTH": 200,
    "AI_MAX_OUTPUT_TOKENS": 120,
    "AI_MAX_TOOL_CALLS": 3,
    "AI_REQUEST_TIMEOUT": 5,
    "AI_RATE_LIMIT": 100,
    "AI_RATE_LIMIT_WINDOW_SECONDS": 60,
    "AI_PILOT_ROLES": ("member", "committee", "treasurer", "chairperson", "admin"),
    "AI_PILOT_USER_IDS": (),
    "AI_PILOT_USERNAMES": (),
}


def read_static(path):
    resolved = finders.find(path)
    if not resolved:
        raise AssertionError(f"Static file not found: {path}")
    with open(resolved, encoding="utf-8") as handle:
        return handle.read()


@override_settings(**AI_UI_SETTINGS)
class TambulAssistantUITests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="phase3_member",
            password="pass1234",
            first_name="Phase",
            last_name="Member",
            role="member",
        )
        self.treasurer = User.objects.create_user(
            username="phase3_treasurer",
            password="pass1234",
            first_name="Phase",
            last_name="Treasurer",
            role="treasurer",
        )

    def test_unauthenticated_user_cannot_access_usable_assistant_ui(self):
        response = self.client.get(reverse("member-dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertNotContains(
            response,
            "data-ai-assistant",
            status_code=302,
        )

    def test_authenticated_member_dashboard_renders_assistant_when_enabled(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("member-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-ai-assistant")
        self.assertContains(response, "Tambul Assistant")
        self.assertContains(response, 'data-chat-url="/ai/chat/"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertNotContains(response, "OPENAI_API_KEY")
        self.assertNotContains(response, "test-openai-key")

    @override_settings(AI_ENABLED=False)
    def test_ai_disabled_hides_assistant_ui(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("member-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-ai-assistant")
        self.assertNotContains(response, "Tambul Assistant")

    @override_settings(
        AI_PILOT_ROLES=("admin", "chairperson", "treasurer"),
        AI_PILOT_USER_IDS=(),
        AI_PILOT_USERNAMES=(),
    )
    def test_non_pilot_member_dashboard_hides_assistant_ui(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("member-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "data-ai-assistant")
        self.assertFalse(ai_assistant_ui_context(self.member)["ai_assistant_enabled"])
        self.assertTrue(ai_assistant_ui_context(self.treasurer)["ai_assistant_enabled"])

    def test_member_suggested_prompts_do_not_include_official_prompts(self):
        prompts = ai_assistant_suggested_prompts(self.member)

        self.assertIn("Have I paid my contribution this month?", prompts)
        self.assertIn("Show me my loan summary.", prompts)
        self.assertIn("What are the latest meeting minutes I can view?", prompts)
        self.assertNotIn("How much is currently outstanding?", prompts)
        self.assertNotIn("Summarize the latest committee meeting.", prompts)
        self.assertNotIn("Give me a financial summary for this month.", prompts)

    def test_authorized_official_gets_role_appropriate_prompts(self):
        prompts = ai_assistant_suggested_prompts(self.treasurer)

        self.assertIn("How are contributions doing this month?", prompts)
        self.assertIn("How are contributions from January to now?", prompts)
        self.assertIn("Who still has outstanding contributions?", prompts)
        self.assertIn("Give me the current loan summary.", prompts)
        self.assertIn("Give me the welfare summary for this year.", prompts)
        self.assertIn("Give me the group financial summary.", prompts)
        self.assertIn("Summarize the latest committee meeting.", prompts)

    def test_assistant_frontend_posts_only_message_to_django_with_csrf(self):
        source = read_static("core/ai_assistant.js")

        self.assertIn('fetch(chatUrl, {', source)
        self.assertIn('"X-CSRFToken": csrfInput?.value || ""', source)
        self.assertIn("body: JSON.stringify({ message })", source)
        self.assertNotIn("api.openai.com", source)
        self.assertNotIn("OPENAI_API_KEY", source)
        self.assertNotIn("user_id", source)
        self.assertNotIn("member_id", source)
        self.assertNotIn("tool_name", source)
        self.assertNotIn("permissions", source)

    def test_assistant_frontend_uses_text_rendering_and_blocks_duplicates(self):
        source = read_static("core/ai_assistant.js")

        self.assertIn("body.textContent = text", source)
        self.assertIn("label.textContent = speaker", source)
        self.assertNotIn("innerHTML", source)
        self.assertIn("state.isSending", source)
        self.assertIn("sendButton.disabled = isBusy", source)
        self.assertIn("AI_RATE_LIMITED", source)
        self.assertIn("AI_PILOT_ACCESS_DENIED", source)

    def test_assistant_template_has_accessible_dialog_controls(self):
        self.client.force_login(self.member)

        response = self.client.get(reverse("member-dashboard"))

        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'aria-label="Close Tambul Assistant"')
        self.assertContains(response, "Message Tambul Assistant")

    def test_frontend_manipulated_identity_fields_remain_rejected(self):
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("ai-chat"),
            data='{"message":"Show me everyone unpaid","role":"treasurer"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "UNSUPPORTED_FIELD")

    def test_ui_route_rendering_does_not_mutate_financial_records(self):
        contribution = Contribution.objects.create(
            member=self.member,
            amount=Decimal("100.00"),
            month=date(2026, 9, 1),
            status="partially_paid",
        )
        loan = Loan.objects.create(
            member=self.member,
            amount=Decimal("1000.00"),
            status="approved",
        )
        welfare = Welfare.objects.create(
            member=self.member,
            description="Phase 3 welfare",
            amount=Decimal("250.00"),
            status="fully_paid",
        )
        payment_intent = PaymentIntent.objects.create(
            member=self.member,
            payment_type="monthly_contribution",
            amount_expected=Decimal("250.00"),
            customer_reference="PHASE3-REF-001",
        )
        self.client.force_login(self.member)

        response = self.client.get(reverse("member-dashboard"))

        self.assertEqual(response.status_code, 200)
        contribution.refresh_from_db()
        loan.refresh_from_db()
        welfare.refresh_from_db()
        payment_intent.refresh_from_db()
        self.assertEqual(contribution.amount, Decimal("100.00"))
        self.assertEqual(contribution.status, "partially_paid")
        self.assertEqual(loan.amount, Decimal("1000.00"))
        self.assertEqual(welfare.status, "fully_paid")
        self.assertEqual(payment_intent.status, "pending")
