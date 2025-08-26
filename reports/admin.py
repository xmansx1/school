from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from .models import Teacher, ActivityReport


# ----------------- إدارة المعلمين -----------------
class TeacherAdmin(UserAdmin):
    model = Teacher
    list_display = ("name", "national_id", "phone", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff")
    search_fields = ("name", "national_id", "phone")
    ordering = ("national_id",)

    fieldsets = (
        (None, {"fields": ("national_id", "password")}),
        ("المعلومات الشخصية", {"fields": ("name", "phone")}),
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
                "national_id",
                "name",
                "phone",
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
        "report_date",   # ✅ التاريخ المدخل من المعلم
        "day_name",      # ✅ اليوم
        "beneficiaries_count",
        "preview_image1",  # ✅ عرض صورة مصغرة
    )
    search_fields = ("program_name", "idea", "teacher__name")
    list_filter = ("report_date", "day_name", "teacher")

    # عرض صورة مصغرة في لوحة الإدارة
    def preview_image1(self, obj):
        if obj.image1:
            return format_html('<img src="{}" width="60" height="60" style="object-fit: cover; border-radius: 6px;" />', obj.image1.url)
        return "لا توجد صورة"
    preview_image1.short_description = "معاينة الصورة"


# ----------------- تسجيل النماذج -----------------
admin.site.register(Teacher, TeacherAdmin)
admin.site.register(ActivityReport, ActivityReportAdmin)
