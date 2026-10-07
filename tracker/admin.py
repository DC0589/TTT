from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Group, GroupMembership, Interview, InterviewRound, InterviewStatus, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Roles", {"fields": ("is_admin", "is_student", "is_hr")}),)
    list_display = ("username", "email", "is_admin", "is_hr", "is_student")


class RoundInline(admin.TabularInline):
    model = InterviewRound
    extra = 0


@admin.register(Interview)
class InterviewAdmin(admin.ModelAdmin):
    list_display = ("company_name", "role", "student", "group", "date_of_interview")
    inlines = [RoundInline]


admin.site.register([Group, GroupMembership, InterviewStatus])
