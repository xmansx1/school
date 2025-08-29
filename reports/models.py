# reports/models.py
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator, MinValueValidator


# =========================
# مستخدم النظام: المعلم
# =========================
class TeacherManager(BaseUserManager):
    def create_user(self, phone, name, password=None, **extra_fields):
        if not phone:
            raise ValueError("رقم الجوال مطلوب")
        if not name:
            raise ValueError("اسم المستخدم مطلوب")
        user = self.model(phone=phone, name=name, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name, password=None, **extra_fields):
        extra_fields.setdefault("role", "manager")
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(phone, name, password, **extra_fields)

ROLE_CHOICES = [
    ("teacher", "معلم"),
    ("manager", "مدير"),
    ("activity_officer", "مسؤول نشاط"),
    ("volunteer_officer", "مسؤول تطوع"),
    ("affairs_officer", "شؤون مدرسية"),
    ("admin_officer", "شؤون إدارية"),
]

ROLE_TO_IS_STAFF = {
    "teacher": False,
    "manager": True,
    "activity_officer": True,
    "volunteer_officer": True,
    "affairs_officer": True,
    "admin_officer": True,
}

class Teacher(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField(max_length=20, unique=True)
    national_id = models.CharField(max_length=20, blank=True, null=True, unique=True)
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default="teacher")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # يتحدد تلقائيًا حسب الدور
    date_joined = models.DateTimeField(auto_now_add=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]

    objects = TeacherManager()

    def save(self, *args, **kwargs):
        # اجعل is_staff متسقًا مع الدور (الأمان وسهولة الإدارة)
        self.is_staff = ROLE_TO_IS_STAFF.get(self.role, False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"


# =========================
# نموذج التقرير العام
# =========================
class Report(models.Model):
    class Category(models.TextChoices):
        ACTIVITY = "activity", "نشاط"
        VOLUNTEER = "volunteer", "تطوع"
        SCHOOL_AFFAIRS = "school_affairs", "شؤون مدرسية"
        ADMIN = "admin", "إدارية"
        EVIDENCE = "evidence", "شواهد"

    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="reports",
        db_index=True,
        verbose_name="المعلم",
    )

    # عنوان عام يصلح لكل التصنيفات (بدلاً من program_name الخاص بالنشاط فقط)
    title = models.CharField(max_length=255, verbose_name="العنوان / البرنامج", db_index=True)

    # تاريخ التقرير/البرنامج
    report_date = models.DateField(verbose_name="تاريخ التقرير / البرنامج", db_index=True)

    # اسم اليوم (يُستنتج تلقائياً من التاريخ إن لم يُدخل)
    day_name = models.CharField(max_length=20, verbose_name="اليوم", blank=True, null=True)

    # حقول مرنة تصلح لجميع الأنواع
    beneficiaries_count = models.PositiveIntegerField(
        verbose_name="عدد المستفيدين",
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        help_text="اتركه فارغًا إذا لا ينطبق",
    )
    idea = models.TextAreaField = models.TextField(  # (TextField) وصف/فكرة التقرير
        verbose_name="الوصف / فكرة التقرير",
        blank=True,
        null=True,
    )

    # التصنيف
    category = models.CharField(
        max_length=32,
        choices=Category.choices,
        default=Category.SCHOOL_AFFAIRS,
        db_index=True,
        verbose_name="التصنيف",
    )

    # صور مرفقة (اعتمد Cloudinary/Storage عبر إعدادات المشروع)
    image1 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image2 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image3 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image4 = models.ImageField(upload_to="reports/", blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["category", "created_at"]),
            models.Index(fields=["teacher", "category"]),
            models.Index(fields=["report_date"]),
        ]
        verbose_name = "تقرير"
        verbose_name_plural = "التقارير"

    def __str__(self):
        return f"{self.title} - {self.get_category_display()} - {self.teacher.name} ({self.report_date})"

    def save(self, *args, **kwargs):
        """
        تعبئة day_name تلقائيًا عند وجود report_date وعدم توفير day_name.
        نستخدم isoweekday(): الاثنين=1 .. الأحد=7
        """
        if self.report_date and not self.day_name:
            days = {
                1: "الاثنين",
                2: "الثلاثاء",
                3: "الأربعاء",
                4: "الخميس",
                5: "الجمعة",
                6: "السبت",
                7: "الأحد",
            }
            self.day_name = days.get(self.report_date.isoweekday())
        super().save(*args, **kwargs)
