from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Additional info", {"fields": ("phone", "national_id")}),
        ("Membership Details", {"fields": ("membership_number", "role")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "password1", "password2", "role", "email", "phone", "membership_number", "national_id"),
        }),
    )

    list_display = ("username", "first_name", "last_name", "role", "is_staff", "membership_number")
    search_fields = ("username", "first_name", "last_name", "email", "membership_number", "national_id")
    ordering = ("first_name",)
