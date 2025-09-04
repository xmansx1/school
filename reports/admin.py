# reports/admin.py
from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html

from .models import Teacher, Report, Ticket, TicketNote


# =========================
# نماذج إدارة المستخدم المخصص (Teacher)
# =========================
class TeacherCreationForm(forms.ModelForm):
    """
    نموذج إنشاء مستخدم في لوحة الإدارة مع حقلي كلمة مرور.
    """
    password1 = forms.CharField(label="كلمة المرور", widget=forms.PasswordInput)
    password2 = forms.CharField(label="تأكيد كلمة المرور", widget=forms.PasswordInput)

    class Meta:
        model = Teacher
        fields = ("phone", "name", "national_id", "role", "is_active", "is_staff")

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("كلمتا المرور غير متطابقتين.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class TeacherChangeForm(forms.ModelForm):
    """
    نموذج تعديل مستخدم في لوحة الإدارة (لا يظهر كلمة المرور الحقيقية).
    """
    class Meta:
        model = Teacher
        fields = (
            "phone",
            "name",
            "national_id",
            "role",
            "is_active",
            "is_staff",
            "is_superuser",
            "groups",
            "user_permissions",
        )


# =========================
# إدارة المعلمين (Teacher)
# =========================
@admin.register(Teacher)
class TeacherAdmin(UserAdmin):
    add_form = TeacherCreationForm
    form = TeacherChangeForm
    model = Teacher

    list_display = ("name", "phone", "national_id", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("name", "phone", "national_id")
    ordering = ("name",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("المعلومات الشخصية", {"fields": ("name", "national_id", "role")}),
        (
            "الصلاحيات",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("تواريخ النظام", {"fields": ("last_login",)}),
    )
    readonly_fields = ("last_login",)

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "phone",
                    "name",
                    "national_id",
                    "role",
                    "password1",
                    "password2",
                    "is_active",
                    "is_staff",
                ),
            },
        ),
    )


# =========================
# إدارة التقارير (Report)
# =========================
@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "teacher",
        "category",
        "report_date",
        "day_name",
        "beneficiaries_count",
        "created_at",
        "preview_image1",
    )
    list_filter = ("category", "report_date", "created_at", "teacher")
    search_fields = (
        "title",
        "idea",
        "teacher__name",
        "teacher__phone",
        "teacher__national_id",
    )
    date_hierarchy = "report_date"
    autocomplete_fields = ("teacher",)
    list_select_related = ("teacher",)
    readonly_fields = ("created_at",)

    def preview_image1(self, obj):
        if getattr(obj, "image1", None):
            url = getattr(getattr(obj, "image1", None), "url", "")
            if url:
                return format_html(
                    '<img src="{}" width="60" height="60" style="object-fit:cover;border-radius:6px;" />',
                    url,
                )
        return "—"

    preview_image1.short_description = "معاينة الصورة"


# =========================
# إدارة التذاكر والملاحظات (Ticket / TicketNote)
# =========================
class TicketNoteInline(admin.TabularInline):
    model = TicketNote
    extra = 0
    fields = ("author", "is_public", "body", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("author",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "status",
        "department",
        "creator",
        "assignee",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "department", "created_at", "updated_at", "assignee")
    search_fields = (
        "id",
        "title",
        "body",
        "creator__name",
        "creator__phone",
        "assignee__name",
        "assignee__phone",
    )
    date_hierarchy = "created_at"
    autocomplete_fields = ("creator", "assignee")
    list_select_related = ("creator", "assignee")
    readonly_fields = ("created_at", "updated_at")
    inlines = (TicketNoteInline,)

    fieldsets = (
        (None, {"fields": ("title", "body", "attachment")}),
        ("الملكية والتعيين", {"fields": ("creator", "assignee", "department")}),
        ("الحالة", {"fields": ("status",)}),
        ("أخرى", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(TicketNote)
class TicketNoteAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "author", "is_public", "created_at")
    list_filter = ("is_public", "created_at", "author")
    search_fields = ("ticket__id", "ticket__title", "body", "author__name")
    autocomplete_fields = ("ticket", "author")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)
