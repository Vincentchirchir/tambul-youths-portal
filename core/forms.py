from django import forms
from .models import Loan, Announcement, MeetingNote

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
        fields = ["title", "description", "content", "file"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Enter meeting title"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Short description"}),
            "content": forms.Textarea(attrs={"class": "form-control", "rows": 6, "placeholder": "Full meeting minutes (optional if uploading a file)"}),
            "file": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }
