from django import forms
from django.contrib.auth import get_user_model

from .models import CommitteeLetter, LetterTemplate, Loan, Announcement, MeetingNote


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
    class Meta:
        model = Loan
        fields = ["amount"]
        widgets = {
            "amount": forms.NumberInput(attrs={
                "class": "form-control",
                "placeholder": "Enter amount (Ksh 1000 - 3000)",
                "min": "100",
                "max": "3000",
                "step": "100",
                "required": True,
            }),
        }

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount < 1000 or amount > 3000:
            raise forms.ValidationError("Loan amount must be between Ksh 1000 and Ksh 3000.")
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
    recipient_name = forms.ChoiceField(
        choices=(),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    recipient_position = forms.ChoiceField(
        choices=RECIPIENT_POSITION_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    class Meta:
        model = CommitteeLetter
        fields = [
            "letter_type",
            "letter_date",
            "recipient_name",
            "recipient_position",
            "recipient_address",
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
        signatory_name = cleaned_data.get("signatory_name")
        signatory_position = cleaned_data.get("signatory_position")

        if not signatory_name or not signatory_position:
            raise forms.ValidationError(
                "Enter the authorized signatory name and position."
            )

        return cleaned_data

    def save(self, commit=True):
        letter = super().save(commit=False)
        letter.recipient_organization = ""
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
