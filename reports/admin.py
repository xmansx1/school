from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Teacher, ActivityReport

class TeacherAdmin(UserAdmin):
    model = Teacher
    list_display = ("name", "national_id", "phone", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff")
    search_fields = ("name", "national_id", "phone")
    ordering = ("national_id",)

    fieldsets = (
        (None, {"fields": ("national_id", "password")}),
        ("المعلومات الشخصية", {"fields": ("name", "phone")}),
        ("الصلاحيات", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("تواريخ النظام", {"fields": ("last_login",)}),
    )

    readonly_fields = ("last_login",)

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("national_id", "name", "phone", "password1", "password2", "is_active", "is_staff"),
        }),
    )

class ActivityReportAdmin(admin.ModelAdmin):
    list_display = ("program_name", "teacher", "date", "beneficiaries_count")
    search_fields = ("program_name", "teacher__name")
    list_filter = ("date", "teacher")

# التسجيل في لوحة الإدارة
admin.site.register(Teacher, TeacherAdmin)
admin.site.register(ActivityReport, ActivityReportAdmin)
