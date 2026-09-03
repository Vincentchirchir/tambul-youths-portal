from django import forms
from django.contrib.auth import get_user_model

from .models import (
    CommitteeLetter,
    InstitutionType,
    LetterRecipientType,
    LetterTemplate,
    Loan,
    Announcement,
    MeetingNote,
)
from .services.loan_limits import (
    LOAN_AMOUNT_STEP,
    LOAN_LIMIT_AMOUNT,
    MIN_LOAN_AMOUNT,
    format_ksh,
    loan_amount_exceeds_message,
    loan_limit_block_message,
    loan_limit_remaining,
)


DEFAULT_RECIPIENT_ADDRESS = "PO BOX 1109 ELDORET"
RECIPIENT_POSITION_CHOICES = [
    ("Member", "Member"),
    ("Committee", "Committee"),
    ("Disciplinary Committee", "Disciplinary Committee"),
]

class ContributionForm(forms.Form):
    PAYMENT_CHOICES = [
        ('monthly', 'Monthly Contribution'),
        ('welfare', 'Welfare'),
    ]
    payment_type = forms.ChoiceField(choices=PAYMENT_CHOICES, required=True)
    amount = forms.DecimalField(min_value=50, decimal_places=2, max_digits=10)

class LoanApplicationForm(forms.ModelForm):
    def __init__(self, *args, member=None, **kwargs):
        self.member = member
        super().__init__(*args, **kwargs)
        self.remaining_loan_limit = (
            loan_limit_remaining(member) if member else LOAN_LIMIT_AMOUNT
        )
        self.fields["amount"].help_text = (
            f"Available loan limit: {format_ksh(self.remaining_loan_limit)}."
        )
        self.fields["amount"].widget.attrs.update({
            "placeholder": (
                f"Enter amount from {format_ksh(MIN_LOAN_AMOUNT)} "
                f"to {format_ksh(self.remaining_loan_limit)}"
            ),
            "min": str(MIN_LOAN_AMOUNT),
            "max": str(self.remaining_loan_limit),
            "step": str(LOAN_AMOUNT_STEP),
            "data-loan-max": str(self.remaining_loan_limit),
            "data-loan-max-display": format_ksh(self.remaining_loan_limit),
            "data-loan-min-display": format_ksh(MIN_LOAN_AMOUNT),
        })

    class Meta:
        model = Loan
        fields = ["amount"]
        widgets = {
            "amount": forms.NumberInput(attrs={
                "class": "form-control",
                "placeholder": "Enter loan amount",
                "min": "100.00",
                "max": "3000.00",
                "step": "100",
                "required": True,
            }),
        }

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        remaining = (
            loan_limit_remaining(self.member)
            if self.member
            else LOAN_LIMIT_AMOUNT
        )
        if remaining < MIN_LOAN_AMOUNT:
            raise forms.ValidationError(loan_limit_block_message())
        if amount < MIN_LOAN_AMOUNT:
            raise forms.ValidationError(
                f"Loan amount must be at least {format_ksh(MIN_LOAN_AMOUNT)}."
            )
        if amount > remaining:
            raise forms.ValidationError(loan_amount_exceeds_message(remaining))
        return amount

class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ["title", "message"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Enter announcement title..."
            }),
            "message": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Write your announcement..."
            }),
        }

class MeetingNoteForm(forms.ModelForm):
    class Meta:
        model = MeetingNote
        fields = ["title", "description", "content", "file", "audience"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter meeting title"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Short description"}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 6, "placeholder": "Full meeting minutes (optional if uploading a file)"}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
            "audience": forms.Select(attrs={"class": "form-control"}),
        }


class CommitteeLetterForm(forms.ModelForm):
    recipient_type = forms.ChoiceField(
        choices=LetterRecipientType.choices,
        widget=forms.Select(attrs={"class": "form-control", "data-recipient-type": "true"}),
    )
    recipient_name = forms.ChoiceField(
        choices=(),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    recipient_position = forms.ChoiceField(
        choices=RECIPIENT_POSITION_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    class Meta:
        model = CommitteeLetter
        fields = [
            "letter_type",
            "letter_date",
            "recipient_type",
            "recipient_name",
            "recipient_position",
            "recipient_address",
            "institution_type",
            "institution_name",
            "institution_department",
            "attention_name",
            "attention_position",
            "institution_address",
            "institution_email",
            "institution_phone",
            "salutation",
            "subject",
            "body",
            "closing_phrase",
            "signatory_name",
            "signatory_position",
            "supporting_attachment",
        ]
        widgets = {
            "letter_type": forms.Select(attrs={"class": "form-control"}),
            "letter_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "recipient_address": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "institution_type": forms.Select(attrs={"class": "form-control"}),
            "institution_name": forms.TextInput(attrs={"class": "form-control"}),
            "institution_department": forms.TextInput(attrs={"class": "form-control"}),
            "attention_name": forms.TextInput(attrs={"class": "form-control"}),
            "attention_position": forms.TextInput(attrs={"class": "form-control"}),
            "institution_address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "institution_email": forms.EmailInput(attrs={"class": "form-control"}),
            "institution_phone": forms.TextInput(attrs={"class": "form-control"}),
            "salutation": forms.TextInput(attrs={"class": "form-control"}),
            "subject": forms.TextInput(attrs={"class": "form-control"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 8}),
            "closing_phrase": forms.TextInput(attrs={"class": "form-control"}),
            "signatory_name": forms.TextInput(attrs={"class": "form-control"}),
            "signatory_position": forms.TextInput(attrs={"class": "form-control"}),
            "supporting_attachment": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["signatory_name"].required = False
        self.fields["signatory_position"].required = False
        self.fields["letter_date"].required = True
        self.fields["recipient_address"].initial = DEFAULT_RECIPIENT_ADDRESS
        self.fields["recipient_position"].initial = "Member"
        self.fields["institution_type"].choices = [("", "Select institution type"), *InstitutionType.choices]
        self.fields["institution_type"].required = False
        self.fields["institution_name"].required = False
        self.fields["institution_department"].required = False
        self.fields["attention_name"].required = False
        self.fields["attention_position"].required = False
        self.fields["institution_address"].required = False
        self.fields["institution_email"].required = False
        self.fields["institution_phone"].required = False
        self.fields["recipient_name"].choices = self._recipient_choices()
        self._preserve_current_recipient_choices()

    def _recipient_choices(self):
        User = get_user_model()
        choices = [("", "Select member")]
        seen_values = {""}
        for user in User.objects.filter(is_active=True).order_by(
            "first_name",
            "last_name",
            "username",
        ):
            value = (user.get_full_name() or user.username).strip()
            if not value or value in seen_values:
                continue
            choices.append((value, str(user)))
            seen_values.add(value)
        return choices

    def _preserve_current_recipient_choices(self):
        if not self.instance or not self.instance.pk:
            return

        current_name = self.instance.recipient_name
        current_position = self.instance.recipient_position
        if current_name and current_name not in dict(self.fields["recipient_name"].choices):
            self.fields["recipient_name"].choices = [
                (current_name, current_name),
                *self.fields["recipient_name"].choices,
            ]
        if current_position and current_position not in dict(RECIPIENT_POSITION_CHOICES):
            self.fields["recipient_position"].choices = [
                (current_position, current_position),
                *RECIPIENT_POSITION_CHOICES,
            ]

    def clean(self):
        cleaned_data = super().clean()
        recipient_type = cleaned_data.get("recipient_type")
        recipient_name = cleaned_data.get("recipient_name")
        recipient_position = cleaned_data.get("recipient_position")
        institution_name = cleaned_data.get("institution_name")
        institution_address = cleaned_data.get("institution_address")
        attention_name = cleaned_data.get("attention_name")
        attention_position = cleaned_data.get("attention_position")
        signatory_name = cleaned_data.get("signatory_name")
        signatory_position = cleaned_data.get("signatory_position")

        if recipient_type == LetterRecipientType.INSTITUTION:
            if not institution_name:
                self.add_error("institution_name", "Enter the institution name.")
            if not institution_address:
                self.add_error("institution_address", "Enter the institution postal address.")
            cleaned_data["recipient_name"] = attention_name or institution_name or ""
            cleaned_data["recipient_position"] = attention_position or ""
        else:
            if not recipient_name:
                self.add_error("recipient_name", "Choose the member recipient.")
            if not recipient_position:
                self.add_error("recipient_position", "Choose the member recipient position.")

        if not signatory_name or not signatory_position:
            raise forms.ValidationError(
                "Enter the authorized signatory name and position."
            )

        return cleaned_data

    def save(self, commit=True):
        letter = super().save(commit=False)
        letter.recipient_organization = ""
        if letter.recipient_type == LetterRecipientType.MEMBER:
            letter.institution_type = ""
            letter.institution_name = ""
            letter.institution_department = ""
            letter.attention_name = ""
            letter.attention_position = ""
            letter.institution_address = ""
            letter.institution_email = ""
            letter.institution_phone = ""
        else:
            letter.recipient_name = self.cleaned_data.get("recipient_name") or letter.institution_name
            letter.recipient_position = self.cleaned_data.get("recipient_position") or ""
        if commit:
            letter.save()
            self.save_m2m()
        return letter

    def clean_body(self):
        body = self.cleaned_data["body"]
        unsafe_tokens = ["<script", "</script", "javascript:"]
        if any(token in body.lower() for token in unsafe_tokens):
            raise forms.ValidationError("Letter body contains unsafe content.")
        return body


class CommitteeLetterTemplateChoiceForm(forms.Form):
    template = forms.ModelChoiceField(
        queryset=LetterTemplate.objects.filter(is_active=True),
        required=False,
        empty_label="Start without a template",
        widget=forms.Select(attrs={"class": "form-control"}),
    )


class CommitteeLetterCommentForm(forms.Form):
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
            }
        ),
    )


class CommitteeLetterReturnForm(CommitteeLetterCommentForm):
    comment = forms.CharField(
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
            }
        ),
    )


class LetterVerificationForm(forms.Form):
    verification_code = forms.CharField(
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "THYG-2026-001-A7F9C2",
            }
        ),
    )
