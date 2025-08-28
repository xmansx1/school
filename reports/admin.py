from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from .models import Teacher, ActivityReport


# ----------------- إدارة المعلمين -----------------
class TeacherAdmin(UserAdmin):
    model = Teacher
    list_display = ("name", "phone", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff")
    search_fields = ("name", "phone")
    ordering = ("phone",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("المعلومات الشخصية", {"fields": ("name",)}),
        ("الصلاحيات", {
            "fields": (
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            )
        }),
        ("تواريخ النظام", {"fields": ("last_login",)}),
    )

    readonly_fields = ("last_login",)

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "phone",
                "name",
                "password1",
                "password2",
                "is_active",
                "is_staff",
            ),
        }),
    )


# ----------------- إدارة التقارير -----------------
class ActivityReportAdmin(admin.ModelAdmin):
    list_display = (
        "program_name",
        "teacher",
        "report_date",     # ✅ التاريخ الصحيح
        "day_name",        # ✅ اليوم الصحيح
        "beneficiaries_count",
        "preview_image1",
    )
    search_fields = ("program_name", "idea", "teacher__name", "teacher__phone")
    list_filter = ("report_date", "day_name", "teacher")

    def preview_image1(self, obj):
        if obj.image1:
            return format_html(
                '<img src="{}" width="60" height="60" style="object-fit: cover; border-radius: 6px;" />',
                obj.image1.url
            )
        return "لا توجد صورة"
    preview_image1.short_description = "معاينة الصورة"


# ----------------- تسجيل النماذج -----------------
admin.site.register(Teacher, TeacherAdmin)
admin.site.register(ActivityReport, ActivityReportAdmin)
