# reports/forms.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional, Tuple

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db.models import Q

# ==============================
# استيرادات الموديلات الأساسية
# ==============================
from .models import (
    Report,
    Teacher,
    Ticket,
    TicketNote,
)

# موارد اختيارية (قد تكون غير موجودة في هذه المرحلة)
try:
    from .models import RequestTicket, REQUEST_DEPARTMENTS  # تراثي للأرشفة
    HAS_REQUEST_TICKET = True
except Exception:
    HAS_REQUEST_TICKET = False

# موارد لوحة الأقسام (قد تُضاف في مرحلة لاحقة ضمن خطة التطوير)
# ندعم الاسمين: DepartmentMember أو DepartmentMembership
Department = None
DEPT_MEMBER_MODEL = None
HAS_DEPARTMENTS = False
try:
    from .models import Department as _Dept  # type: ignore
    Department = _Dept
    HAS_DEPARTMENTS = True
    try:
        from .models import DepartmentMember as _DeptMember  # type: ignore
        DEPT_MEMBER_MODEL = _DeptMember
    except Exception:
        try:
            from .models import DepartmentMembership as _DeptMember  # type: ignore
            DEPT_MEMBER_MODEL = _DeptMember
        except Exception:
            DEPT_MEMBER_MODEL = None
except Exception:
    HAS_DEPARTMENTS = False

# موارد أنواع التقارير (اختياري)
try:
    from .models import ReportType  # type: ignore
    HAS_REPORTTYPE = True
except Exception:
    ReportType = None  # type: ignore
    HAS_REPORTTYPE = False


# ==============================
# أدوات تحقق عامّة
# ==============================
digits10 = RegexValidator(r"^\d{10}$", "يجب أن يتكون من 10 أرقام")
sa_phone = RegexValidator(r"^0\d{9}$", "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام")

# الأدوار التي تعتبر ضمن طاقم الإدارة (is_staff=True)
STAFF_ROLES = {
    "manager",
    "activity_officer",
    "volunteer_officer",
    "affairs_officer",
    "admin_officer",
}

# مصدر اختيارات الدور الاحتياطية
def _static_role_choices_from_model() -> List[Tuple[str, str]]:
    """
    يعيد أدوار الموديل (إن وُجدت) كاختيارات ثابتة لاستخدامها كقاعدة،
    مثل teacher/manager وبقية الأدوار القديمة.
    """
    try:
        return list(Teacher.Role.choices)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from .models import ROLE_CHOICES as ROLE_CHOICES_CONST  # type: ignore
        return list(ROLE_CHOICES_CONST)
    except Exception:
        pass
    return [
        ("teacher", "المعلم"),
        ("manager", "المدير"),
        ("activity_officer", "مسؤول النشاط"),
        ("volunteer_officer", "مسؤول التطوع"),
        ("affairs_officer", "مسؤول الشؤون المدرسية"),
        ("admin_officer", "مسؤول الشؤون الإدارية"),
    ]


# ========== أدوات مساعدة ديناميكية ==========
def _report_type_choices() -> List[Tuple[str, str]]:
    """
    اختيارات أنواع التقارير من ReportType إن وُجد (code, name) مع تحصين ضد الحقول الاختيارية.
    """
    if not HAS_REPORTTYPE or ReportType is None:
        return []
    try:
        qs = ReportType.objects.all()

        # إن وُجد is_active فعّل الفلترة
        if hasattr(ReportType, "is_active"):
            qs = qs.filter(is_active=True)

        # ترتيب آمن حسب الحقول المتاحة
        order_fields = []
        if hasattr(ReportType, "order"):
            order_fields.append("order")
        if hasattr(ReportType, "name"):
            order_fields.append("name")
        if order_fields:
            qs = qs.order_by(*order_fields)

        items: List[Tuple[str, str]] = []
        for r in qs:
            code = getattr(r, "code", None)
            name = getattr(r, "name", None) or (code or "")
            code = (code or "").strip()
            if code:
                items.append((code, name))
        return items
    except Exception:
        return []


def _legacy_category_choices() -> List[Tuple[str, str]]:
    """قراءة اختيارات Report.category القديمة كاحتياطي."""
    try:
        field = Report._meta.get_field("category")
        ch = list(getattr(field, "choices", []))
        return [(v, l) for (v, l) in ch if v not in ("", None)]
    except Exception:
        return []


def _existing_report_categories_distinct() -> List[Tuple[str, str]]:
    """
    المصدر الأخير: يستخرج القيم الفعلية الموجودة في التقارير (distinct)
    لاستخدامها كخيارات عندما لا يتوفر ReportType ولا choices قديمة.
    """
    try:
        qs = (
            Report.objects.exclude(category__isnull=True)
            .exclude(category__exact="")
            .values_list("category", flat=True)
            .distinct()
        )
        items: List[Tuple[str, str]] = []
        seen = set()
        for code in qs:
            c = (code or "").strip()
            if c and c not in seen:
                items.append((c, c))  # نستخدم الكود نفسه كوسم عرض مؤقتًا
                seen.add(c)
        return items
    except Exception:
        return []


def _department_role_choices() -> List[Tuple[str, str]]:
    """
    يبني اختيارات الدور القادمة من الأقسام:
    القيمة = slug، المعروض = role_label إن وُجد وإلا name.
    """
    if not HAS_DEPARTMENTS or Department is None:
        return []
    try:
        qs = Department.objects.filter(is_active=True)
        result: List[Tuple[str, str]] = []
        for d in qs:
            label = getattr(d, "role_label", None) or getattr(d, "name", None) or getattr(d, "slug", "")
            code = (getattr(d, "slug", "") or "").strip()
            if code:
                result.append((code, label))
        return result
    except Exception:
        return []


def _dynamic_role_choices() -> List[Tuple[str, str]]:
    """
    يدمج الأدوار الأساسية + أدوار الأقسام الفعّالة، مع إزالة التكرارات والمحافظة على الترتيب.
    """
    base = _static_role_choices_from_model()
    seen = set()
    merged: List[Tuple[str, str]] = []
    for v, l in base:
        if v not in seen:
            merged.append((v, l))
            seen.add(v)
    for v, l in _department_role_choices():
        if v not in seen:
            merged.append((v, l))
            seen.add(v)
    return merged


def _department_choices_for_forms(with_placeholder: bool = True) -> List[Tuple[str, str]]:
    """
    اختيارات حقل (القسم) في النماذج:
    - إن وُجد موديل Department: نستخدم الأقسام الفعّالة (slug/label).
    - وإلا: نحاول قراءة choices من حقل Ticket.department أو REQUEST_DEPARTMENTS (تراثي).
    """
    items: List[Tuple[str, str]] = []
    if HAS_DEPARTMENTS and Department is not None:
        items = _department_role_choices()
    else:
        try:
            field = Ticket._meta.get_field("department")
            model_choices = list(getattr(field, "choices", []))
            items = [(v, l) for (v, l) in model_choices if v not in ("", None)]
        except Exception:
            items = []
        if not items:
            try:
                items = list(REQUEST_DEPARTMENTS)  # type: ignore
            except Exception:
                pass

    if with_placeholder:
        return [("", "— اختر القسم —")] + items
    return items


def _teachers_for_dept(dept_code: str):
    """
    يُرجع QuerySet للمعلمين المنتمين إلى قسم معيّن إما:
    - بدورهم Teacher.role == dept_code
    - أو عبر عضوية DepartmentMember/DepartmentMembership (إن وُجدت)
    """
    base = Teacher.objects.filter(is_active=True)
    if not dept_code:
        return base.none()

    q = Q(role=dept_code)
    if HAS_DEPARTMENTS and Department is not None and DEPT_MEMBER_MODEL is not None:
        dep_fk_name = tea_fk_name = None
        try:
            for f in DEPT_MEMBER_MODEL._meta.get_fields():  # type: ignore[attr-defined]
                if getattr(f, "is_relation", False) and getattr(f, "remote_field", None):
                    if getattr(f.remote_field, "model", None) is Department and dep_fk_name is None:
                        dep_fk_name = f.name
                    if getattr(f.remote_field, "model", None) is Teacher and tea_fk_name is None:
                        tea_fk_name = f.name
                if dep_fk_name and tea_fk_name:
                    break
        except Exception:
            dep_fk_name = tea_fk_name = None

        if dep_fk_name and tea_fk_name:
            try:
                dept_obj = Department.objects.filter(slug=dept_code).first()
                if dept_obj:
                    mem_qs = DEPT_MEMBER_MODEL.objects.filter(  # type: ignore[attr-defined]
                        **{dep_fk_name: dept_obj}
                    ).values_list(tea_fk_name, flat=True)
                    q |= Q(id__in=mem_qs)
            except Exception:
                pass

    return base.filter(q).only("id", "name", "role").order_by("name")


def _is_teacher_in_dept(teacher: Teacher, dept_code: str) -> bool:
    """
    يتحقق هل المعلّم ضمن القسم بالدور أو بعضوية.
    """
    if not teacher or not dept_code:
        return False
    if getattr(teacher, "role", None) == dept_code:
        return True
    if HAS_DEPARTMENTS and Department is not None and DEPT_MEMBER_MODEL is not None:
        dep_fk_name = tea_fk_name = None
        try:
            for f in DEPT_MEMBER_MODEL._meta.get_fields():  # type: ignore[attr-defined]
                if getattr(f, "is_relation", False) and getattr(f, "remote_field", None):
                    if getattr(f.remote_field, "model", None) is Department and dep_fk_name is None:
                        dep_fk_name = f.name
                    if getattr(f.remote_field, "model", None) is Teacher and tea_fk_name is None:
                        tea_fk_name = f.name
                if dep_fk_name and tea_fk_name:
                    break
        except Exception:
            dep_fk_name = tea_fk_name = None

        if dep_fk_name and tea_fk_name:
            try:
                dept_obj = Department.objects.filter(slug=dept_code).first()
                if dept_obj:
                    exists = DEPT_MEMBER_MODEL.objects.filter(  # type: ignore[attr-defined]
                        **{dep_fk_name: dept_obj, tea_fk_name: teacher}
                    ).exists()
                    if exists:
                        return True
            except Exception:
                pass
    return False


# ==============================
# 📌 نموذج التقرير العام (محدّث)
# ==============================
class ReportForm(forms.ModelForm):
    # ChoiceField واجهة فقط – الخيارات تُستمد من قواعد متعددة (ReportType/choices/البيانات)
    category = forms.ChoiceField(
        required=True,
        label="نوع التقرير",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

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
                    "placeholder": "العنوان / اسم البرنامج",
                    "maxlength": "150",
                }
            ),
            "report_date": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "day_name": forms.TextInput(
                attrs={
                    "class": "input",
                    "readonly": "readonly",
                    "placeholder": "يُولَّد تلقائيًا من التاريخ",
                }
            ),
            "beneficiaries_count": forms.NumberInput(
                attrs={"class": "input", "min": "0", "inputmode": "numeric"}
            ),
            "idea": forms.Textarea(
                attrs={"class": "textarea", "rows": 4, "placeholder": "محتوى التقرير"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # (1) من ReportType (المفعّلة إن وُجدت)
        rt_choices = _report_type_choices()

        # (2) من choices القديمة على الحقل
        legacy = _legacy_category_choices()

        # (3) fallback من قيم موجودة فعليًا في التقارير (لا يُستخدم إلا إذا كان المصدران السابقان فارغين)
        from_existing = _existing_report_categories_distinct() if (not rt_choices and not legacy) else []

        # دمج المصادر بالترتيب، مع إزالة التكرارات
        seen = set()
        choices: List[Tuple[str, str]] = []
        for v, l in (rt_choices + legacy + from_existing):
            v = (v or "").strip()
            if v and v not in seen:
                choices.append((v, l))
                seen.add(v)

        # تضمين قيمة السجل الجاري تحريره إن لم تكن موجودة
        if self.is_bound:
            current_val = (self.data.get("category") or "").strip()
        else:
            current_val = (getattr(self.instance, "category", "") or "").strip()
        if current_val and current_val not in seen:
            choices.append((current_val, current_val))
            seen.add(current_val)

        # تعيين الخيارات مع placeholder
        self.fields["category"].choices = [("", "— اختر نوع التقرير —")] + choices

        # في صفحة الإضافة، اجعل الابتدائي فارغًا
        if not self.is_bound and not getattr(self.instance, "pk", None):
            self.initial["category"] = ""

    def clean_category(self):
        value = (self.cleaned_data.get("category") or "").strip()
        if not value:
            raise ValidationError("يُرجى اختيار نوع التقرير.")
        return value

    def clean_beneficiaries_count(self):
        val = self.cleaned_data.get("beneficiaries_count")
        if val is None:
            return val
        if val < 0:
            raise ValidationError("عدد المستفيدين لا يمكن أن يكون سالبًا.")
        return val

    def clean(self):
        cleaned = super().clean()

        # قيود الصور
        for f in ["image1", "image2", "image3", "image4"]:
            img = cleaned.get(f)
            if img:
                if hasattr(img, "size") and img.size > 2 * 1024 * 1024:
                    self.add_error(f, "حجم الصورة أكبر من 2MB")
                ctype = getattr(img, "content_type", "")
                if ctype and not str(ctype).startswith("image/"):
                    self.add_error(f, "الملف يجب أن يكون صورة صالحة")

        # توليد اليوم من التاريخ
        report_date = cleaned.get("report_date")
        if report_date:
            days = {
                1: "الاثنين",
                2: "الثلاثاء",
                3: "الأربعاء",
                4: "الخميس",
                5: "الجمعة",
                6: "السبت",
                7: "الأحد",
            }
            cleaned["day_name"] = days.get(report_date.isoweekday())
        return cleaned


# ==============================
# 📌 نموذج إدارة المعلّم
# ==============================
class TeacherForm(forms.ModelForm):
    password = forms.CharField(
        label="كلمة المرور",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "اتركه فارغًا للإبقاء على كلمة المرور الحالية",
                "autocomplete": "new-password",
            }
        ),
    )

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10,
        max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "05XXXXXXXX",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"0\d{9}",
                "title": "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام",
            }
        ),
    )

    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10,
        max_length=10,
        validators=[digits10],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "رقم الهوية (10 أرقام)",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"\d{10}",
                "title": "رقم الهوية يجب أن يتكون من 10 أرقام",
            }
        ),
    )

    role = forms.ChoiceField(
        label="الدور",
        choices=[],  # سنضبطها ديناميكيًا في __init__
        widget=forms.Select(attrs={"class": "form-select"}),
        required=True,
    )

    is_active = forms.BooleanField(
        label="نشط",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "national_id", "role", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "الاسم الكامل للمعلم",
                    "maxlength": "150",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = _dynamic_role_choices()

    def clean_password(self):
        pwd = (self.cleaned_data.get("password") or "")
        return pwd if pwd.strip() else ""

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if len(phone) != 10:
            raise ValidationError("رقم الجوال يجب أن يتكون من 10 أرقام.")
        return phone

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if len(nid) != 10:
            raise ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام.")
        return nid

    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        new_pwd = self.cleaned_data.get("password")
        if new_pwd:
            instance.set_password(new_pwd)
        elif self.instance and self.instance.pk:
            instance.password = self.instance.password
        try:
            instance.is_staff = (instance.role in STAFF_ROLES)
        except Exception:
            pass
        if commit:
            instance.save()
        return instance


# ==============================
# 📌 تذاكر — إنشاء/إجراء/ملاحظات
# ==============================
class TicketCreateForm(forms.ModelForm):
    """
    نموذج إنشاء تذكرة على موديل Ticket.
    - يعرض الأقسام ديناميكيًا (Department إن وُجد، وإلا من حقل الموديل/التراثي).
    - يفلتر المستلمين حسب القسم المختار (role أو العضوية).
    """
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
            "department": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(attrs={"class": "input", "placeholder": "عنوان الطلب"}),
            "body": forms.Textarea(
                attrs={"class": "textarea", "rows": 4, "placeholder": "تفاصيل الطلب"}
            ),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        self.fields["department"].choices = _department_choices_for_forms(with_placeholder=True)

        if self.is_bound:
            dept_value = (self.data.get("department") or "").strip()
        else:
            dept_value = getattr(self.instance, "department", None)

        if dept_value:
            self.fields["assignee"].queryset = _teachers_for_dept(dept_value)
        else:
            self.fields["assignee"].queryset = Teacher.objects.none()

    def clean(self):
        cleaned = super().clean()
        dept = (cleaned.get("department") or "").strip()
        assignee: Optional[Teacher] = cleaned.get("assignee")
        if assignee and dept and not _is_teacher_in_dept(assignee, dept):
            self.add_error("assignee", "الموظّف المختار لا ينتمي إلى هذا القسم.")
        return cleaned


class TicketActionForm(forms.Form):
    status = forms.ChoiceField(
        choices=Ticket.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": "input"}),
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3, "class": "textarea", "placeholder": "اكتب ملاحظة (تظهر للمرسل)"}
        ),
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
            "body": forms.Textarea(
                attrs={"rows": 3, "class": "textarea", "placeholder": "أضف ملاحظة"}
            ),
        }


# ==============================
# 📌 نموذج الطلب التراثي (RequestTicket)
# ==============================
if HAS_REQUEST_TICKET:
    class RequestTicketForm(forms.ModelForm):
        department = forms.ChoiceField(
            choices=[],  # سنحددها ديناميكياً
            required=True,
            widget=forms.Select(attrs={"class": "form-select", "required": "required"}),
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
                "title": forms.TextInput(
                    attrs={"class": "input", "placeholder": "عنوان مختصر"}
                ),
                "body": forms.Textarea(
                    attrs={"class": "textarea", "rows": 5, "placeholder": "اكتب تفاصيل الطلب..."}
                ),
            }

        def __init__(self, *args, **kwargs):
            kwargs.pop("user", None)
            super().__init__(*args, **kwargs)

            self.fields["department"].choices = _department_choices_for_forms(with_placeholder=True)

            dept_value = None
            if self.is_bound:
                dept_value = (self.data.get("department") or "").strip()
            elif getattr(self.instance, "pk", None):
                dept_value = getattr(self.instance, "department", None)

            if dept_value:
                qs = _teachers_for_dept(dept_value)
                self.fields["assignee"].queryset = qs
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
    class RequestTicketForm(forms.Form):
        title = forms.CharField(disabled=True)
        body = forms.CharField(widget=forms.Textarea, disabled=True)
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.add_error(None, "نموذج الطلب التراثي غير مفعّل في هذا المشروع.")


# ==============================
# 📌 نماذج إدارة الأقسام (اختياري — تتوفر عند وجود الموديلات)
# ==============================
if HAS_DEPARTMENTS and Department is not None:
    class DepartmentForm(forms.ModelForm):
        """
        يعرض role_label/description تلقائيًا إن كانت موجودة في الموديل.
        """
        class Meta:
            model = Department
            fields: List[str] = ["name", "slug"]
            if hasattr(Department, "role_label"):
                fields.append("role_label")
            if hasattr(Department, "description"):
                fields.append("description")
            fields.append("is_active")

        def clean_slug(self):
            s = (self.cleaned_data.get("slug") or "").strip().lower()
            if not s:
                raise ValidationError("الـ slug مطلوب.")
            return s

    class DepartmentAssignForm(forms.Form):
        teacher = forms.ModelChoiceField(
            queryset=Teacher.objects.filter(is_active=True).only("id", "name").order_by("name"),
            label="المعلم",
        )
        try:
            role_type = forms.ChoiceField(
                choices=DEPT_MEMBER_MODEL.ROLE_TYPE_CHOICES,  # type: ignore[attr-defined]
                label="نوع التكليف",
            )
        except Exception:
            role_type = forms.CharField(label="نوع التكليف")
else:
    class DepartmentForm(forms.Form):
        name = forms.CharField(disabled=True)
        slug = forms.CharField(disabled=True)
        is_active = forms.BooleanField(required=False, disabled=True)

    class DepartmentAssignForm(forms.Form):
        teacher = forms.CharField(disabled=True)
        role_type = forms.CharField(disabled=True)


# ==============================
# 📌 نماذج إدارة أنواع التقارير (ReportType) — اختياري
# ==============================
if HAS_REPORTTYPE and ReportType is not None:
    class ReportTypeForm(forms.ModelForm):
        class Meta:
            model = ReportType
            fields = ["name", "code", "description", "order", "is_active"]

        def clean_code(self):
            s = (self.cleaned_data.get("code") or "").strip().lower()
            if not s:
                raise ValidationError("حقل الكود (code) مطلوب.")
            return s
else:
    class ReportTypeForm(forms.Form):
        name = forms.CharField(disabled=True)
        code = forms.CharField(disabled=True)
        is_active = forms.BooleanField(required=False, disabled=True)
