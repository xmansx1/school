# reports/models.py
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MinValueValidator, FileExtensionValidator
from django.db import models


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
    ("teacher", "المعلم"),
    ("manager", "مدير"),
    ("activity_officer", "مسؤول النشاط"),
    ("volunteer_officer", "مسؤول التطوع"),
    ("affairs_officer", "مسؤول الشؤون المدرسية"),
    ("admin_officer", "مسؤول الشؤون الإدارية"),
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
    phone = models.CharField("رقم الجوال", max_length=20, unique=True)
    national_id = models.CharField("الهوية الوطنية", max_length=20, blank=True, null=True, unique=True)
    name = models.CharField("الاسم", max_length=150, db_index=True)
    role = models.CharField("الدور", max_length=32, choices=ROLE_CHOICES, default="teacher")
    is_active = models.BooleanField("نشط", default=True)
    is_staff = models.BooleanField("موظّف لوحة", default=False)  # يُحدَّث تلقائيًا حسب الدور
    date_joined = models.DateTimeField("تاريخ الانضمام", auto_now_add=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]

    objects = TeacherManager()

    class Meta:
        verbose_name = "مستخدم (معلم)"
        verbose_name_plural = "المستخدمون"

    def save(self, *args, **kwargs):
        # مزامنة is_staff مع الدور
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
        verbose_name="المعلم (حساب)",
    )

    # اسم المعلم المقروء وقت إنشاء التقرير (تجميد للاسم)
    teacher_name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="اسم المعلم (وقت الإنشاء)",
        help_text="يُحفظ هنا الاسم الظاهر في التقرير بغض النظر عن تغيّر اسم الحساب لاحقًا.",
    )

    title = models.CharField("العنوان / البرنامج", max_length=255, db_index=True)
    report_date = models.DateField("تاريخ التقرير / البرنامج", db_index=True)
    day_name = models.CharField("اليوم", max_length=20, blank=True, null=True)

    beneficiaries_count = models.PositiveIntegerField(
        "عدد المستفيدين",
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        help_text="اتركه فارغًا إذا لا ينطبق",
    )

    idea = models.TextField("الوصف / فكرة التقرير", blank=True, null=True)

    # لا نضع default لتجبر المعلم على الاختيار
    category = models.CharField(
        "التصنيف",
        max_length=32,
        choices=Category.choices,
        db_index=True,
        blank=False,
        null=False,
    )

    image1 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image2 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image3 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image4 = models.ImageField(upload_to="reports/", blank=True, null=True)

    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)

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
        display_name = self.teacher_name.strip() if self.teacher_name else self.teacher.name
        return f"{self.title} - {self.get_category_display()} - {display_name} ({self.report_date})"

    @property
    def teacher_display_name(self) -> str:
        """اسم المعلم المعروض للتقارير/الطباعة."""
        return (self.teacher_name or self.teacher.name or "").strip()

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


# =========================
# منظومة التذاكر الموحّدة
# =========================
class Ticket(models.Model):
    class Department(models.TextChoices):
        ADMIN = "admin_officer", "الشؤون الإدارية"
        AFFAIRS = "affairs_officer", "الشؤون المدرسية"
        ACTIVITY = "activity_officer", "النشاط"
        VOLUNTEER = "volunteer_officer", "التطوع"
        MANAGER = "manager", "المدير"
        TEACHER = "teacher", "المعلمين"

    class Status(models.TextChoices):
        OPEN = "open", "جديد"
        IN_PROGRESS = "in_progress", "قيد المعالجة"
        DONE = "done", "مكتمل"
        REJECTED = "rejected", "مرفوض"

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tickets_created",
        verbose_name="المرسل",
        db_index=True,
    )
    department = models.CharField(
        "القسم",
        max_length=32,
        choices=Department.choices,
        db_index=True,
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="tickets_assigned",
        verbose_name="المستلم",
        blank=True,
        null=True,
        db_index=True,
    )
    title = models.CharField("عنوان الطلب", max_length=255)
    body = models.TextField("تفاصيل الطلب", blank=True, null=True)
    attachment = models.FileField(
        "مرفق",
        upload_to="tickets/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "doc", "docx"])],
    )
    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "طلب"
        verbose_name_plural = "الطلبات"
        indexes = [
            models.Index(fields=["department", "status", "created_at"]),
            models.Index(fields=["assignee", "status"]),
        ]

    def __str__(self):
        return f"Ticket #{self.pk} - {self.title[:40]}"


class TicketNote(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="notes", verbose_name="التذكرة")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ticket_notes", verbose_name="كاتب الملاحظة")
    body = models.TextField("الملاحظة")
    is_public = models.BooleanField("ظاهرة للمرسل؟", default=True)
    created_at = models.DateTimeField("تاريخ الإضافة", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ملاحظة طلب"
        verbose_name_plural = "ملاحظات الطلبات"

    def __str__(self):
        return f"Note #{self.pk} on Ticket #{self.ticket_id}"


# =========================
# الأقسام والتكليف (للوحة المدير)
# =========================
class Department(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=64, unique=True)  # يُستخدم كقيمة الدور
    role_label = models.CharField(
        max_length=120, blank=True,
        help_text="الاسم الذي سيظهر في قائمة (الدور). إن تُرك فارغًا سيُستخدم اسم القسم."
    )
    is_active = models.BooleanField(default=True)
    # ... أي حقول أخرى لديك

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.role_label:
            self.role_label = self.name
        self.slug = (self.slug or "").strip().lower()
        super().save(*args, **kwargs)


class DepartmentMembership(models.Model):
    TEACHER = "teacher"
    OFFICER = "officer"
    ROLE_TYPE_CHOICES = [(TEACHER, "Teacher"), (OFFICER, "Officer")]

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="memberships", verbose_name="القسم")
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dept_memberships", verbose_name="المعلم")
    role_type = models.CharField("نوع التكليف", max_length=16, choices=ROLE_TYPE_CHOICES, default=TEACHER)

    class Meta:
        unique_together = [("department", "teacher")]
        indexes = [
            models.Index(fields=["department"]),
            models.Index(fields=["teacher"]),
        ]
        verbose_name = "تكليف قسم"
        verbose_name_plural = "تكليفات الأقسام"

    def __str__(self):
        return f"{self.teacher} @ {self.department} ({self.role_type})"


# =========================
# نماذج تراثية للأرشفة (إن وُجدت بيانات قديمة)
# =========================
REQUEST_DEPARTMENTS = [
    ("manager", "المدير"),
    ("activity_officer", "مسؤول النشاط"),
    ("volunteer_officer", "مسؤول التطوع"),
    ("affairs_officer", "مسؤول الشؤون المدرسية"),
    ("admin_officer", "مسؤول الشؤون الإدارية"),
]


class RequestTicket(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "جديد"
        IN_PROGRESS = "in_progress", "قيد المعالجة"
        DONE = "done", "تم الإنجاز"
        REJECTED = "rejected", "مرفوض"

    requester = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="created_tickets",
        verbose_name="صاحب الطلب",
        db_index=True,
    )
    department = models.CharField("القسم/الجهة", max_length=32, choices=REQUEST_DEPARTMENTS, db_index=True)
    assignee = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        related_name="assigned_tickets",
        verbose_name="المستلم",
        null=True,
        blank=True,
    )
    title = models.CharField("عنوان الطلب", max_length=200)
    body = models.TextField("تفاصيل الطلب")
    attachment = models.FileField(
        "مرفق (اختياري)",
        upload_to="tickets/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "doc", "docx"])],
    )
    status = models.CharField("الحالة", max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["assignee", "status"]),
        ]
        verbose_name = "طلب (تراثي)"
        verbose_name_plural = "طلبات (تراثية)"

    def __str__(self):
        return f"#{self.pk} - {self.title} ({self.get_status_display()})"


class RequestLog(models.Model):
    ticket = models.ForeignKey(RequestTicket, on_delete=models.CASCADE, related_name="logs", verbose_name="الطلب")
    actor = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="منفّذ العملية")
    old_status = models.CharField("الحالة القديمة", max_length=20, choices=RequestTicket.Status.choices, blank=True)
    new_status = models.CharField("الحالة الجديدة", max_length=20, choices=RequestTicket.Status.choices, blank=True)
    note = models.TextField("ملاحظة", blank=True)
    created_at = models.DateTimeField("وقت الإنشاء", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "سجل طلب (تراثي)"
        verbose_name_plural = "سجل الطلبات (تراثي)"

    def __str__(self):
        return f"Log for #{self.ticket_id} at {self.created_at:%Y-%m-%d %H:%M}"


# reports/models.py (إضافة موديل اختياري)
from django.db import models

class ReportType(models.Model):
    code = models.SlugField(max_length=40, unique=True)  # قيمة تُخزن في Report.category
    name = models.CharField(max_length=120)              # الاسم المعروض
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("order", "name")
        verbose_name = "نوع تقرير"
        verbose_name_plural = "أنواع التقارير"

    def __str__(self) -> str:
        return self.name or self.code
