# reports/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

from .models import Report, Teacher, ROLE_CHOICES  # ROLE_CHOICES كاحتياطي لو لم تتوفر Teacher.Role

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

# مصدر اختيارات الدور
try:
    ROLE_CHOICES_FORM = Teacher.Role.choices  # Django choices Enum
except Exception:
    ROLE_CHOICES_FORM = ROLE_CHOICES  # احتياطي من الموديل


# ==============================
# 📌 نموذج التقرير العام
# ==============================
class ReportForm(forms.ModelForm):
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
                "placeholder": "العنوان / اسم البرنامج",
                "maxlength": "150",
            }),
            "report_date": forms.DateInput(attrs={
                "class": "input",
                "type": "date",
            }),
            "day_name": forms.TextInput(attrs={
                "class": "input",
                "readonly": "readonly",
                "placeholder": "يُولَّد تلقائيًا من التاريخ",
            }),
            "beneficiaries_count": forms.NumberInput(attrs={
                "class": "input",
                "min": "0",
                "inputmode": "numeric",
            }),
            "idea": forms.Textarea(attrs={
                "class": "textarea",
                "rows": 4,
                "placeholder": "محتوى التقرير",
            }),
            "category": forms.Select(attrs={"class": "form-select"}),
        }

    def clean_beneficiaries_count(self):
        val = self.cleaned_data.get("beneficiaries_count")
        if val is None:
            return val
        if val < 0:
            raise ValidationError("عدد المستفيدين لا يمكن أن يكون سالبًا.")
        return val

    def clean(self):
        cleaned = super().clean()

        # ✅ قيود الصور: حجم ≤ 2MB ونوع صورة
        for f in ["image1", "image2", "image3", "image4"]:
            img = cleaned.get(f)
            if img:
                if hasattr(img, "size") and img.size > 2 * 1024 * 1024:
                    self.add_error(f, "حجم الصورة أكبر من 2MB")
                ctype = getattr(img, "content_type", "")
                if ctype and not str(ctype).startswith("image/"):
                    self.add_error(f, "الملف يجب أن يكون صورة صالحة")

        # ✅ توليد اسم اليوم من التاريخ إن توفر
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
# 📌 نموذج إدارة المعلّم (أدوار متعددة)
# ==============================
class TeacherForm(forms.ModelForm):
    # حقل واجهة فقط لتغيير كلمة المرور عند الحاجة
    password = forms.CharField(
        label="كلمة المرور",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "اتركه فارغًا للإبقاء على كلمة المرور الحالية",
            "autocomplete": "new-password",
        }),
    )

    # نعيد تعريف الحقول لإضافة تحقق خادم صارم ومظهر موحّد
    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10, max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "05XXXXXXXX",
            "maxlength": "10",
            "inputmode": "numeric",
            "pattern": r"0\d{9}",
            "title": "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام",
        }),
    )

    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10, max_length=10,
        validators=[digits10],
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "رقم الهوية (10 أرقام)",
            "maxlength": "10",
            "inputmode": "numeric",
            "pattern": r"\d{10}",
            "title": "رقم الهوية يجب أن يتكون من 10 أرقام",
        }),
    )

    role = forms.ChoiceField(
        label="الدور",
        choices=ROLE_CHOICES_FORM,
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
        # ملاحظة: لا نضع password ضمن الحقول المربوطة بالموديل
        fields = ["name", "phone", "national_id", "role", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "الاسم الكامل للمعلم",
                "maxlength": "150",
            }),
        }

    # -------- تحققات إضافية --------
    def clean_password(self):
        """إن كانت كلمة المرور فارغة أو مسافات فقط نُرجِع سلسلة فارغة (لا تغيير)."""
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

    # -------- الحفظ --------
    def save(self, commit: bool = True):
        """
        - لا نغيّر كلمة المرور إن تُركت فارغة.
        - نضبط is_staff تلقائيًا حسب الدور (إن لم يكن الموديل يضبطها داخليًا).
        """
        instance: Teacher = super().save(commit=False)

        # كلمة المرور
        new_pwd = self.cleaned_data.get("password")
        if new_pwd:
            instance.set_password(new_pwd)
        elif self.instance and self.instance.pk:
            # حافظ على الهاش القديم إن كان تعديلًا
            instance.password = self.instance.password

        # is_staff بناءً على الدور
        try:
            instance.is_staff = (instance.role in STAFF_ROLES)
        except Exception:
            # في حال كان للموديل منطق خاصه، تجاهل
            pass

        if commit:
            instance.save()
        return instance
