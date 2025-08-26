from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

# مدير المستخدمين (للمعلمين)
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.core.validators import RegexValidator


class TeacherManager(BaseUserManager):
    def create_user(self, national_id, name, phone=None, password=None, **extra_fields):
        if not national_id:
            raise ValueError("يجب إدخال رقم الهوية")
        if not name:
            raise ValueError("يجب إدخال اسم المعلم")

        user = self.model(
            national_id=national_id,
            name=name,
            phone=phone,
            **extra_fields
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, national_id, name, phone=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(national_id, name, phone, password, **extra_fields)


class Teacher(AbstractBaseUser, PermissionsMixin):
    national_id = models.CharField(
        max_length=10, unique=True,
        validators=[
            RegexValidator(r'^\d{10}$', "رقم الهوية يجب أن يكون 10 أرقام فقط")
        ],
        verbose_name="رقم الهوية"
    )
    phone = models.CharField(
        max_length=10, blank=True, null=True,
        validators=[
            RegexValidator(r'^0\d{9}$', "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام")
        ],
        verbose_name="رقم الجوال"
    )
    name = models.CharField(max_length=100, verbose_name="اسم المعلم")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = TeacherManager()

    USERNAME_FIELD = "national_id"
    REQUIRED_FIELDS = ["name", "phone"]

    def __str__(self):
        return self.name

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
