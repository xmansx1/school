# reports/forms.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, List, Tuple

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

# ==============================
# استيراد الموديلات
# ==============================
from .models import (
    Teacher,
    Role,
    Department,
    DepartmentMembership,
    ReportType,
    Report,
    Ticket,
    TicketNote,
)

# (تراثي – اختياري)
try:
    from .models import RequestTicket, REQUEST_DEPARTMENTS  # type: ignore
    HAS_REQUEST_TICKET = True
except Exception:
    RequestTicket = None  # type: ignore
    REQUEST_DEPARTMENTS = []  # type: ignore
    HAS_REQUEST_TICKET = False


# ==============================
# أدوات تحقق عامة (SA-specific)
# ==============================
digits10 = RegexValidator(r"^\d{10}$", "يجب أن يتكون من 10 أرقام.")
sa_phone = RegexValidator(r"^0\d{9}$", "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام.")


# ==============================
# مساعدات داخلية للأقسام/المستخدمين
# ==============================
def _teachers_for_dept(dept_slug: str):
    """
    إرجاع QuerySet للمعلمين المنتمين لقسم معيّن.
    - عبر Role.slug = dept_slug (منطقي بسيط ومباشر).
    - أو عبر عضوية DepartmentMembership (department ←→ teacher).
    """
    if not dept_slug:
        return Teacher.objects.none()

    q = Q(role__slug=dept_slug)

    # عضوية القسم (إن وُجد القسم)
    dep = Department.objects.filter(slug=dept_slug).first()
    if dep:
        teacher_ids = DepartmentMembership.objects.filter(department=dep).values_list("teacher_id", flat=True)
        q |= Q(id__in=teacher_ids)

    return Teacher.objects.filter(is_active=True).filter(q).only("id", "name").order_by("name").distinct()


def _is_teacher_in_dept(teacher: Teacher, dept_slug: str) -> bool:
    """
    يحدد ما إذا كان المعلم ينتمي للقسم المحدد:
    - عن طريق دور المستخدم Role.slug.
    - أو عضوية DepartmentMembership.
    """
    if not teacher or not dept_slug:
        return False

    # عبر الدور
    if getattr(getattr(teacher, "role", None), "slug", None) == dept_slug:
        return True

    # عبر العضوية
    dep = Department.objects.filter(slug=dept_slug).first()
    if not dep:
        return False
    return DepartmentMembership.objects.filter(department=dep, teacher=teacher).exists()


# ==============================
# 📌 نموذج التقرير العام
# ==============================
class ReportForm(forms.ModelForm):
    """
    يعتمد اعتمادًا كاملاً على ReportType (ديناميكي من قاعدة البيانات)
    ويستخدم قيمة code كقيمة ثابتة في الخيارات (to_field_name="code").
    """
    class Meta:
        model = Report
        fields = [
            "title",
            "report_date",
            "day_name",
            "beneficiaries_count",
            "idea",
            "category",
            "image1", "image2", "image3", "image4",
        ]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "input",
                "placeholder": "العنوان / البرنامج",
                "maxlength": "255",
                "autocomplete": "off",
            }),
            "report_date": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "day_name": forms.TextInput(attrs={"class": "input", "readonly": "readonly"}),
            "beneficiaries_count": forms.NumberInput(attrs={"class": "input", "min": "0", "inputmode": "numeric"}),
            "idea": forms.Textarea(attrs={"class": "textarea", "rows": 4, "placeholder": "الوصف / فكرة التقرير"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # التصنيف ديناميكي دائمًا من ReportType (نشط فقط) — بالقيمة code
        self.fields["category"] = forms.ModelChoiceField(
            label="نوع التقرير",
            queryset=ReportType.objects.filter(is_active=True).order_by("order", "name"),
            required=True,               # يمكن جعله False إذا رغبت السماح بدون تصنيف
            empty_label="— اختر نوع التقرير —",
            to_field_name="code",
            widget=forms.Select(attrs={"class": "form-select"}),
        )

    def clean_beneficiaries_count(self):
        val = self.cleaned_data.get("beneficiaries_count")
        if val is None:
            return val
        if val < 0:
            raise ValidationError("عدد المستفيدين لا يمكن أن يكون سالبًا.")
        return val

    def clean(self):
        cleaned = super().clean()
        # قيود الصور (الحجم ≤ 2MB وأن تكون صورة)
        for f in ["image1", "image2", "image3", "image4"]:
            img = cleaned.get(f)
            if img:
                if hasattr(img, "size") and img.size > 2 * 1024 * 1024:
                    self.add_error(f, "حجم الصورة أكبر من 2MB.")
                ctype = getattr(img, "content_type", "")
                if ctype and not str(ctype).startswith("image/"):
                    self.add_error(f, "الملف يجب أن يكون صورة صالحة.")
        # ملاحظة: اسم اليوم يُملأ تلقائيًا في model.save() إذا كان فارغًا.
        return cleaned


# ==============================
# 📌 نموذج إدارة المعلّم
# ==============================
class TeacherForm(forms.ModelForm):
    """
    Teacher.role هو FK → Role (ديناميكي).
    نعرض الأدوار بقيمة slug (to_field_name="slug") لثباتها عبر البيئات.
    """
    password = forms.CharField(
        label="كلمة المرور",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "اتركه فارغًا للإبقاء على الحالية",
            "autocomplete": "new-password",
        }),
    )

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10,
        max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "05XXXXXXXX",
            "maxlength": "10",
            "inputmode": "numeric",
            "pattern": r"0\d{9}",
            "autocomplete": "off",
        }),
    )

    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10,
        max_length=10,
        validators=[digits10],
        required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "رقم الهوية (10 أرقام)",
            "maxlength": "10",
            "inputmode": "numeric",
            "pattern": r"\d{10}",
            "autocomplete": "off",
        }),
    )

    role = forms.ModelChoiceField(
        label="الدور",
        queryset=Role.objects.all().order_by("name"),
        required=False,
        empty_label="—",
        to_field_name="slug",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "national_id", "role", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "الاسم الكامل", "maxlength": "150"}),
        }

    def clean_password(self):
        pwd = (self.cleaned_data.get("password") or "").strip()
        return pwd or ""

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if len(phone) != 10:
            raise ValidationError("رقم الجوال يجب أن يتكون من 10 أرقام.")
        return phone

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if nid and len(nid) != 10:
            raise ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام.")
        return nid or None

    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        new_pwd = self.cleaned_data.get("password")
        if new_pwd:
            instance.set_password(new_pwd)
        elif self.instance and self.instance.pk:
            # الإبقاء على كلمة المرور الحالية إن لم تُدخل واحدة جديدة
            instance.password = self.instance.password
        # is_staff يُحدَّث تلقائيًا داخل model.save() حسب role.is_staff_by_default
        if commit:
            instance.save()
        return instance


# ==============================
# 📌 تذاكر — إنشاء/إجراءات/ملاحظات
# ==============================
class TicketCreateForm(forms.ModelForm):
    """
    نموذج إنشاء/تعديل التذكرة:
    - department: ModelChoiceField على Department بالقيمة slug (to_field_name="slug").
    - assignee: يُفلتر تلقائيًا على أعضاء القسم (بالدور أو العضوية).
    """
    department = forms.ModelChoiceField(
        label="القسم",
        queryset=Department.objects.filter(is_active=True).order_by("name"),
        required=False,              # اجعله True إذا رغبت فرض قسم
        empty_label="— اختر القسم —",
        to_field_name="slug",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    assignee = forms.ModelChoiceField(
        queryset=Teacher.objects.none(),
        required=False,
        label="المستلم",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Ticket
        fields = ["department", "assignee", "title", "body", "attachment"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "input", "placeholder": "عنوان الطلب", "maxlength": "255", "autocomplete": "off"}),
            "body": forms.Textarea(attrs={"class": "textarea", "rows": 4, "placeholder": "تفاصيل الطلب"}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)  # لا نحتاجه هنا؛ يُمرر في save إن رغبت
        super().__init__(*args, **kwargs)

        # أثناء الإنشاء أو التحرير: جهّز قائمة المستلمين
        if self.is_bound:
            dept_value = (self.data.get("department") or "").strip()
        else:
            # عند التحرير: slug إذا FK
            current_dept = getattr(self.instance, "department", None)
            dept_value = getattr(current_dept, "slug", None)

        if dept_value:
            self.fields["assignee"].queryset = _teachers_for_dept(dept_value)
        else:
            self.fields["assignee"].queryset = Teacher.objects.none()

    def clean(self):
        cleaned = super().clean()
        dept = cleaned.get("department")
        assignee: Optional[Teacher] = cleaned.get("assignee")

        dept_slug: Optional[str] = getattr(dept, "slug", None) if isinstance(dept, Department) else None
        if assignee and dept_slug and not _is_teacher_in_dept(assignee, dept_slug):
            self.add_error("assignee", "الموظّف المختار لا ينتمي إلى هذا القسم.")
        return cleaned

    def save(self, commit=True, user=None):
        obj: Ticket = super().save(commit=False)
        if user is not None and not obj.pk:
            obj.creator = user
        if not getattr(obj, "status", None):
            try:
                obj.status = Ticket.Status.OPEN  # type: ignore[attr-defined]
            except Exception:
                pass
        if commit:
            obj.save()
        return obj


class TicketActionForm(forms.Form):
    status = forms.ChoiceField(
        choices=Ticket.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": "input"}),
        label="تغيير الحالة",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "textarea", "placeholder": "اكتب ملاحظة (تظهر للمرسل)"}),
        label="ملاحظة",
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("status") and not (cleaned.get("note") or "").strip():
            raise forms.ValidationError("أدخل ملاحظة أو غيّر الحالة.")
        return cleaned


class TicketNoteForm(forms.ModelForm):
    class Meta:
        model = TicketNote
        fields = ["body", "is_public"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 3, "class": "textarea", "placeholder": "أضف ملاحظة"}),
        }


# ==============================
# 📌 نموذج الطلب التراثي (اختياري)
# ==============================
if HAS_REQUEST_TICKET and RequestTicket is not None:

    class RequestTicketForm(forms.ModelForm):
        department = forms.ChoiceField(
            choices=[],
            required=True,
            widget=forms.Select(attrs={"class": "form-select"}),
            label="القسم",
        )
        assignee = forms.ModelChoiceField(
            queryset=Teacher.objects.none(),
            required=False,
            widget=forms.Select(attrs={"class": "form-select"}),
            label="المستلم",
        )

        class Meta:
            model = RequestTicket
            fields = ["department", "assignee", "title", "body", "attachment"]
            widgets = {
                "title": forms.TextInput(attrs={"class": "input", "placeholder": "عنوان مختصر", "maxlength": "200"}),
                "body": forms.Textarea(attrs={"class": "textarea", "rows": 5, "placeholder": "اكتب تفاصيل الطلب..."}),
            }

        def __init__(self, *args, **kwargs):
            kwargs.pop("user", None)
            super().__init__(*args, **kwargs)

            # مصادر الاختيارات لقسم تراثي
            choices: List[Tuple[str, str]] = []
            try:
                field = RequestTicket._meta.get_field("department")
                model_choices = list(getattr(field, "choices", []))
                choices = [(v, l) for (v, l) in model_choices if v not in ("", None)]
            except Exception:
                if REQUEST_DEPARTMENTS:
                    choices = list(REQUEST_DEPARTMENTS)
            self.fields["department"].choices = [("", "— اختر القسم —")] + choices

            # إعداد assignee بحسب القسم
            if self.is_bound:
                dept_value = (self.data.get("department") or "").strip()
            elif getattr(self.instance, "pk", None):
                dept_value = getattr(self.instance, "department", None)
            else:
                dept_value = ""

            if dept_value:
                qs = _teachers_for_dept(dept_value)
                self.fields["assignee"].queryset = qs
                # إن كان هناك مستخدم وحيد مناسب، عيّنه افتراضيًا عند التحرير
                if qs.count() == 1 and not self.is_bound and not getattr(self.instance, "assignee_id", None):
                    self.initial["assignee"] = qs.first().pk
            else:
                self.fields["assignee"].queryset = Teacher.objects.none()

        def clean(self):
            cleaned = super().clean()
            dept = (cleaned.get("department") or "").strip()
            assignee: Optional[Teacher] = cleaned.get("assignee")
            if dept:
                qs = _teachers_for_dept(dept)
                if qs.count() > 1 and assignee is None:
                    self.add_error("assignee", "يرجى اختيار الموظّف المستلم.")
                if assignee and not _is_teacher_in_dept(assignee, dept):
                    self.add_error("assignee", "الموظّف المختار لا ينتمي إلى هذا القسم.")
            return cleaned

else:
    # في حال إزالة النماذج التراثية من المشروع
    class RequestTicketForm(forms.Form):
        title = forms.CharField(disabled=True)
        body = forms.CharField(widget=forms.Textarea, disabled=True)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.add_error(None, "نموذج الطلب التراثي غير مفعّل في هذا المشروع.")
