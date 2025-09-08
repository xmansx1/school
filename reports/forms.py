# reports/forms.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, List, Tuple

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
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

    return (
        Teacher.objects.filter(is_active=True)
        .filter(q)
        .only("id", "name")
        .order_by("name")
        .distinct()
    )


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
            "image1",
            "image2",
            "image3",
            "image4",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "input",
                    "placeholder": "العنوان / البرنامج",
                    "maxlength": "255",
                    "autocomplete": "off",
                }
            ),
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
            required=True,  # غيّرها إلى False إذا رغبت السماح بدون تصنيف
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
# 📌 نموذج إدارة المعلّم (إضافة/تعديل)
# ==============================
TEACHERS_DEPT_SLUGS = {"teachers", "معلمين", "المعلمين"}


# داخل reports/forms.py

# أقسام تعتبر "قسم المعلّمين"
TEACHERS_DEPT_SLUGS = {"teachers", "معلمين", "المعلمين"}

class TeacherForm(forms.ModelForm):
    """
    إنشاء/تعديل معلّم:
    - إن كان القسم من أقسام "المعلمين" → الدور داخل القسم يقتصر على (معلم) فقط.
    - بقية الأقسام: الخيارات (مسؤول القسم | موظف/معلم) كما هي.
    - يضبط Teacher.role تلقائيًا:
        • قسم المعلمين → Role.slug='teacher' إن وُجد.
        • غير ذلك → Role.slug = department.slug (إن وُجد).
    - ينشئ/يحدّث DepartmentMembership (department, teacher, role_type).
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

    department = forms.ModelChoiceField(
        label="القسم",
        queryset=Department.objects.filter(is_active=True).order_by("name"),
        required=True,
        empty_label="— اختر القسم —",
        to_field_name="slug",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_department"}),
    )

    membership_role = forms.ChoiceField(
        label="الدور داخل القسم",
        choices=[],  # تُضبط ديناميكيًا في __init__
        required=True,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_membership_role"}),
    )

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10, max_length=10,
        widget=forms.TextInput(attrs={
            "class": "form-control", "placeholder": "05XXXXXXXX", "maxlength": "10",
            "inputmode": "numeric", "pattern": r"0\d{9}", "autocomplete": "off"
        }),
    )
    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10, max_length=10, required=False,
        widget=forms.TextInput(attrs={
            "class": "form-control", "placeholder": "رقم الهوية (10 أرقام)",
            "maxlength": "10", "inputmode": "numeric", "pattern": r"\d{10}",
            "autocomplete": "off"
        }),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "national_id", "is_active", "department", "membership_role"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "الاسم الكامل", "maxlength": "150"}),
        }

    # --- خيارات الأدوار (ثابتة) ---
    ROLE_CHOICES_ALL = (
        (DepartmentMembership.OFFICER, "مسؤول القسم"),
        (DepartmentMembership.TEACHER, "موظف/معلم"),
    )
    ROLE_CHOICES_TEACHERS_ONLY = (
        (DepartmentMembership.TEACHER, "معلم"),
    )

    # --- أدوات داخلية ---
    def _current_department_slug(self) -> Optional[str]:
        """
        يستنتج slug القسم الحالي من:
        1) POST عند الربط.
        2) initial عند التحرير.
        3) instance (عضوية/دور).
        """
        # 1) من البيانات المرتبطة
        if self.is_bound:
            val = (self.data.get("department") or "").strip()
            if val:
                return val.lower()

        # 2) من initial
        init_dep = (self.initial.get("department") or "")
        if init_dep:
            return str(init_dep).lower()

        # 3) من instance
        dep_slug = None
        if getattr(self.instance, "pk", None):
            # من أول عضوية
            try:
                memb = self.instance.dept_memberships.select_related("department").first()  # type: ignore[attr-defined]
                if memb and getattr(memb.department, "slug", None):
                    dep_slug = memb.department.slug
            except Exception:
                dep_slug = None
            # أو من الدور العام
            if not dep_slug:
                dep_slug = getattr(getattr(self.instance, "role", None), "slug", None)

        return (dep_slug or "").lower() or None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ضبط خيارات "الدور داخل القسم" حسب القسم الحالي
        dep_slug = self._current_department_slug()
        if dep_slug and dep_slug in {s.lower() for s in TEACHERS_DEPT_SLUGS}:
            self.fields["membership_role"].choices = self.ROLE_CHOICES_TEACHERS_ONLY
            self.initial.setdefault("membership_role", DepartmentMembership.TEACHER)
        else:
            self.fields["membership_role"].choices = self.ROLE_CHOICES_ALL

    # ---- تحقق أساسي ----
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

    def clean(self):
        cleaned = super().clean()
        dep: Optional[Department] = cleaned.get("department")
        role_in_dept: str = (cleaned.get("membership_role") or "").strip()

        # منع اختيار "مسؤول" عند قسم المعلمين (تحقق خادمي)
        if dep and dep.slug in TEACHERS_DEPT_SLUGS and role_in_dept == DepartmentMembership.OFFICER:
            self.add_error("membership_role", "قسم المعلّمين لا يملك مسؤول قسم. الدور المتاح: معلم فقط.")
        return cleaned

    # ---- حفظ مع ربط الدور/العضوية ----
    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        new_pwd = (self.cleaned_data.get("password") or "").strip()
        dep: Optional[Department] = self.cleaned_data.get("department")

        # كلمة المرور
        if new_pwd:
            instance.set_password(new_pwd)
        elif self.instance and self.instance.pk:
            instance.password = self.instance.password  # إبقاء كلمة المرور

        # تعيين Teacher.role وفقًا للقسم المختار
        target_role = None
        if dep:
            if dep.slug in TEACHERS_DEPT_SLUGS:
                target_role = Role.objects.filter(slug="teacher").first()
            else:
                target_role = Role.objects.filter(slug=dep.slug).first()
        instance.role = target_role  # قد تكون None إذا لم يوجد الدور

        # تحديد الدور داخل القسم النهائي (فرض "معلم" لقسم المعلمين)
        if dep and dep.slug in TEACHERS_DEPT_SLUGS:
            role_in_dept = DepartmentMembership.TEACHER
        else:
            role_in_dept = self.cleaned_data.get("membership_role") or DepartmentMembership.TEACHER

        with transaction.atomic():
            instance.save()

            # أنشئ/حدّث عضوية القسم للمستخدم
            if dep:
                DepartmentMembership.objects.update_or_create(
                    department=dep,
                    teacher=instance,
                    defaults={"role_type": role_in_dept},
                )

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
        required=False,  # اجعله True إذا رغبت فرض قسم
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
            "title": forms.TextInput(
                attrs={"class": "input", "placeholder": "عنوان الطلب", "maxlength": "255", "autocomplete": "off"}
            ),
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


# ==============================
# 📌 نموذج إدارة القسم (اختيار أنواع التقارير)
# ==============================
class DepartmentForm(forms.ModelForm):
    """
    نموذج إدارة القسم مع اختيار أنواع التقارير المسموح بها لهذا القسم.
    سيُزامن الدور تلقائيًا عبر إشعار m2m في models.py.
    """
    reporttypes = forms.ModelMultipleChoiceField(
        label="أنواع التقارير المرتبطة",
        queryset=ReportType.objects.filter(is_active=True).order_by("order", "name"),
        required=False,
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select",
                "size": "8",  # واجهة مريحة للاختيار المتعدد
                "aria-label": "اختر نوع/أنواع التقارير للقسم",
            }
        ),
        help_text="المسؤولون عن هذا القسم سيشاهدون التقارير من هذه الأنواع فقط.",
    )

    class Meta:
        model = Department
        fields = ["name", "slug", "role_label", "is_active", "reporttypes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "maxlength": "120"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "maxlength": "64"}),
            "role_label": forms.TextInput(attrs={"class": "form-control", "maxlength": "120"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip().lower()
        if not slug:
            # اسم بسيط آمن، يمكن توليد slug تلقائيًا
            from django.utils.text import slugify

            slug = slugify(self.cleaned_data.get("name") or "", allow_unicode=True)
        # تأكد من عدم التضارب مع أقسام أخرى:
        qs = Department.objects.filter(slug=slug)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("المعرّف (slug) مستخدم مسبقًا لقسم آخر.")
        return slug


from django import forms
from django.utils import timezone
from .models import Notification, NotificationRecipient, Teacher
from .permissions import restrict_queryset_for_user  # إن رغبت الاستفادة من هذا التصميم لقوائم المعلمين

class NotificationCreateForm(forms.Form):
    title = forms.CharField(max_length=120, required=False, label="عنوان (اختياري)")
    message = forms.CharField(widget=forms.Textarea(attrs={"rows":5}), label="نص الإشعار")
    is_important = forms.BooleanField(required=False, initial=False, label="مهم")
    expires_at = forms.DateTimeField(required=False, label="ينتهي في (اختياري)",
                                     widget=forms.DateTimeInput(attrs={"type":"datetime-local"}))
    teachers = forms.ModelMultipleChoiceField(
        queryset=Teacher.objects.none(),
        required=True,
        label="المستلمون (يمكن اختيار أكثر من معلم)",
        widget=forms.SelectMultiple(attrs={"size":12})
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # حصر المعلمين حسب صلاحية المُنشئ:
        qs = Teacher.objects.filter(is_active=True).order_by("name")
        # إن كان المُنشئ Officer، قصِر القائمة على معلمي قسمه فقط:
        try:
            role_slug = getattr(getattr(user, "role", None), "slug", None)
            if role_slug and role_slug not in (None, "manager"):
                # استخدم منطقك الحالي لجلب أعضاء القسم
                from .views import _user_department_codes
                codes = _user_department_codes(user)
                if codes:
                    qs = qs.filter(
                        models.Q(role__slug__in=codes) |
                        models.Q(dept_memberships__department__slug__in=codes)  # إن كانت لديك علاقة عضويات
                    ).distinct()
        except Exception:
            pass

        self.fields["teachers"].queryset = qs

    def save(self, creator):
        cleaned = self.cleaned_data
        n = Notification.objects.create(
            title=cleaned.get("title") or "",
            message=cleaned["message"],
            is_important=bool(cleaned.get("is_important")),
            expires_at=cleaned.get("expires_at") or None,
            created_by=creator,
        )
        teachers = list(cleaned["teachers"])
        if teachers:
            NotificationRecipient.objects.bulk_create([
                NotificationRecipient(notification=n, teacher=t) for t in teachers
            ], ignore_conflicts=True)
        return n
