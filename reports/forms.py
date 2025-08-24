from django import forms
from .models import ActivityReport

class ActivityReportForm(forms.ModelForm):
    class Meta:
        model = ActivityReport
        fields = ["program_name", "beneficiaries_count", "idea", "image1", "image2", "image3", "image4"]
        widgets = {
            "program_name": forms.TextInput(attrs={"class": "input", "placeholder": "اسم البرنامج"}),
            "beneficiaries_count": forms.NumberInput(attrs={"class": "input", "min": "0", "inputmode": "numeric"}),
            "idea": forms.Textarea(attrs={"class": "textarea", "rows": 4, "placeholder": "وصف مختصر للفكرة"}),
        }

    def clean(self):
        cleaned = super().clean()
        for f in ["image1", "image2", "image3", "image4"]:
            img = cleaned.get(f)
            if img and img.size > 2 * 1024 * 1024:  # 2MB
                self.add_error(f, "حجم الصورة أكبر من 2MB")
        return cleaned
from django import forms
from .models import Teacher


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
        widget=forms.CheckboxInput(attrs={"class": "toggle-switch"})
    )

    is_staff = forms.ChoiceField(
        label="نوع الحساب",
        choices=[("0", "معلم"), ("1", "مدير")],
        widget=forms.RadioSelect(attrs={"class": "role-radio"}),
        required=True,
    )

    class Meta:
        model = Teacher
        fields = ["name", "national_id", "phone", "password", "is_active", "is_staff"]

        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "اسم المعلم"
            }),
            "national_id": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "رقم الهوية (10 أرقام)",
                "maxlength": "10",
                "pattern": r"\d{10}",
                "title": "رقم الهوية يجب أن يتكون من 10 أرقام فقط"
            }),
            "phone": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "رقم الجوال (مثال: 05XXXXXXXX)",
                "maxlength": "10",
                "pattern": r"0\d{9}",
                "title": "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام"
            }),
        }
