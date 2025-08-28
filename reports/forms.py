from django import forms
from .models import ActivityReport
import datetime

class ActivityReportForm(forms.ModelForm):
    class Meta:
        model = ActivityReport
        fields = [
            "program_name", 
            "report_date",   # ✅ التاريخ المدخل من المعلم
            "day_name",      # ✅ اليوم يظهر تلقائياً أو يمكن تعديله
            "beneficiaries_count", 
            "idea", 
            "image1", "image2", "image3", "image4"
        ]
        widgets = {
            "program_name": forms.TextInput(attrs={
                "class": "input", 
                "placeholder": "اسم البرنامج"
            }),
            "report_date": forms.DateInput(attrs={
                "class": "input", 
                "type": "date"
            }),
            "day_name": forms.TextInput(attrs={
                "class": "input", 
                "readonly": "readonly",  # يظهر فقط - يمنع التعديل
                "placeholder": "سيتم توليد اليوم تلقائياً"
            }),
            "beneficiaries_count": forms.NumberInput(attrs={
                "class": "input", 
                "min": "0", 
                "inputmode": "numeric"
            }),
            "idea": forms.Textarea(attrs={
                "class": "textarea", 
                "rows": 4, 
                "placeholder": "وصف مختصر للفكرة"
            }),
        }

    def clean(self):
        cleaned = super().clean()

        # ✅ التأكد من حجم الصور
        for f in ["image1", "image2", "image3", "image4"]:
            img = cleaned.get(f)
            if img and img.size > 2 * 1024 * 1024:  # 2MB
                self.add_error(f, "حجم الصورة أكبر من 2MB")

        # ✅ حساب اليوم تلقائياً عند إدخال التاريخ
        report_date = cleaned.get("report_date")
        if report_date:
            days = ["الاثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت","الأحد"]
            cleaned["day_name"] = days[report_date.weekday()]

        return cleaned


from django import forms
from .models import Teacher


# reports/forms.py
class TeacherForm(forms.ModelForm):
    password = forms.CharField(
        label="كلمة المرور",
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "أدخل كلمة المرور"
        }),
        required=False,
    )

    is_active = forms.BooleanField(
        label="نشط",
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"})
    )

    is_staff = forms.ChoiceField(
        label="نوع الحساب",
        choices=[("0", "معلم"), ("1", "مدير")],
        widget=forms.RadioSelect(attrs={"class": "role-radio"}),
        required=True,
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "password", "is_active", "is_staff"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "اسم المعلم"
            }),
            "phone": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "رقم الجوال (مثال: 05XXXXXXXX)",
                "maxlength": "10",
                "pattern": r"0\d{9}",
                "title": "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام"
            }),
        }

    def save(self, commit=True):
        teacher = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            teacher.set_password(password)  # تشفير كلمة المرور
        if commit:
            teacher.save()
        return teacher
