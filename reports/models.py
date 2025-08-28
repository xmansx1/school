from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

# مدير المستخدمين (للمعلمين)
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator


class TeacherManager(BaseUserManager):
    def create_user(self, phone, name, password=None, **extra_fields):
        if not phone:
            raise ValueError("يجب إدخال رقم الجوال")
        if not name:
            raise ValueError("يجب إدخال اسم المعلم")

        user = self.model(
            phone=phone,
            name=name,
            **extra_fields
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if not password:
            raise ValueError("يجب تعيين كلمة مرور للمشرف")
        return self.create_user(phone, name, password, **extra_fields)

class Teacher(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(
        max_length=10, unique=True,
        validators=[
            RegexValidator(r'^0\d{9}$', "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام")
        ],
        verbose_name="رقم الجوال"
    )
    name = models.CharField(max_length=100, verbose_name="اسم المعلم")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = TeacherManager()

    USERNAME_FIELD = "phone"   # ✅ تسجيل الدخول برقم الجوال
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return f"{self.name} ({self.phone})"

# نموذج التقرير
class ActivityReport(models.Model):
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="reports")
    program_name = models.CharField(max_length=255, verbose_name="البرنامج")
    
    # تاريخ البرنامج (يدخله المعلّم بنفسه)
    report_date = models.DateField(verbose_name="تاريخ البرنامج")
    
    # اليوم (يتم توليده تلقائيًا عند الحفظ أو تعبئته من الفورم)
    day_name = models.CharField(max_length=20, verbose_name="اليوم", blank=True, null=True)

    beneficiaries_count = models.PositiveIntegerField(verbose_name="عدد المستفيدين")
    idea = models.TextField(verbose_name="فكرة البرنامج")
    
    image1 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image2 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image3 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image4 = models.ImageField(upload_to="reports/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # لو لم يتم إدخال اليوم، نستنتجه من التاريخ
        if self.report_date and not self.day_name:
            days = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"]
            self.day_name = days[self.report_date.weekday()]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.program_name} - {self.teacher.name} ({self.report_date})"
