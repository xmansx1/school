# reports/models.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import MinValueValidator, FileExtensionValidator
from django.db import models, transaction
from django.utils.text import slugify


# =========================
# مرجع الأدوار الديناميكي
# =========================
class Role(models.Model):
    slug = models.SlugField("المعرّف (slug)", max_length=64, unique=True)
    name = models.CharField("الاسم", max_length=120)

    # يمنح الوصول للوحة التحكم افتراضيًا للمستخدمين الذين يحملون هذا الدور
    is_staff_by_default = models.BooleanField("يمتلك لوحة التحكم افتراضيًا؟", default=False)

    # يرى كل أنواع التقارير (يتجاوز القيود التفصيلية)
    can_view_all_reports = models.BooleanField("يشاهد كل التصنيفات؟", default=False)

    # أنواع التقارير المسموح لهذا الدور برؤيتها (عند تعطيل can_view_all_reports)
    # (تُعرّف أسفلًا: ReportType — نستخدم مرجعًا نصيًا)
    allowed_reporttypes = models.ManyToManyField(
        "ReportType",
        blank=True,
        related_name="roles_allowed",
        verbose_name="الأنواع المسموح بها",
    )

    is_active = models.BooleanField("نشط", default=True)

    class Meta:
        ordering = ("slug",)
        verbose_name = "دور"
        verbose_name_plural = "الأدوار"

    def __str__(self) -> str:
        return self.name or self.slug

    def save(self, *args, **kwargs):
        # تطبيع slug إلى lowercase بدون فراغات
        if self.slug:
            self.slug = self.slug.strip().lower()
        super().save(*args, **kwargs)


# =========================
# مستخدم النظام: المعلم
# =========================
class TeacherManager(BaseUserManager):
    def create_user(self, phone, name, password=None, **extra_fields):
        if not phone:
            raise ValueError("رقم الجوال مطلوب")
        if not name:
            raise ValueError("اسم المستخدم مطلوب")
        user = self.model(phone=phone.strip(), name=name.strip(), **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # إن وُجد دور manager نربطه، وإلا نمشي بدون دور
        try:
            mgr = Role.objects.filter(slug="manager").first()
            if mgr:
                extra_fields.setdefault("role", mgr)
        except Exception:
            pass
        return self.create_user(phone, name, password, **extra_fields)


class Teacher(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField("رقم الجوال", max_length=20, unique=True)
    national_id = models.CharField("الهوية الوطنية", max_length=20, blank=True, null=True, unique=True)
    name = models.CharField("الاسم", max_length=150, db_index=True)

    # دور ديناميكي من المنصّة
    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="الدور",
        related_name="users",
    )

    is_active = models.BooleanField("نشط", default=True)
    # يُحدَّث تلقائيًا حسب الدور.is_staff_by_default
    is_staff = models.BooleanField("موظّف لوحة", default=False)
    date_joined = models.DateTimeField("تاريخ الانضمام", auto_now_add=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]

    objects = TeacherManager()

    class Meta:
        verbose_name = "مستخدم (معلم)"
        verbose_name_plural = "المستخدمون"

    @property
    def role_display(self) -> str:
        """اسم الدور للعرض في القوالب."""
        return getattr(self.role, "name", "-")

    def save(self, *args, **kwargs):
        # مزامنة is_staff مع الدور إن وُجد
        try:
            if self.role is not None:
                self.is_staff = bool(self.role.is_staff_by_default)
        except Exception:
            pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({getattr(self.role, 'name', 'بدون دور')})"


# =========================
# مرجع الأقسام الديناميكي
# =========================
class Department(models.Model):
    name = models.CharField("اسم القسم", max_length=120)
    slug = models.SlugField("المعرّف (slug)", max_length=64, unique=True)
    role_label = models.CharField(
        "الاسم الظاهر في قائمة (الدور)",
        max_length=120,
        blank=True,
        help_text="هذا الاسم سيظهر كخيار (دور) عند إضافة المعلّم. إن تُرك فارغًا سيُستخدم اسم القسم.",
    )
    is_active = models.BooleanField("نشط", default=True)

    class Meta:
        ordering = ("id",)
        verbose_name = "قسم"
        verbose_name_plural = "الأقسام"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        """
        مزامنة جدول Role مع كل قسم:
        - ينشئ/يحدّث Role بنفس slug.
        - name للدور يأتي من role_label (أو name إن كان role_label فارغًا).
        - يحدّث is_active للدور مطابقًا للقسم.
        - في حال تغيير slug للقسم، نحدّث الدور القديم بدل إنشاء دور جديد.
        """
        # تطبيع الحقول
        if not self.role_label:
            self.role_label = self.name
        if self.slug:
            self.slug = self.slug.strip().lower()
        else:
            self.slug = slugify(self.name or "", allow_unicode=True)

        old_slug = None
        if self.pk:
            old_slug = Department.objects.filter(pk=self.pk).values_list("slug", flat=True).first()

        role_name = (self.role_label or self.name or "").strip()

        with transaction.atomic():
            super().save(*args, **kwargs)

            # --- مزامنة الدور المطابق ---
            # ملاحظة: نستخدم Role مباشرة لأنه مُعرّف أعلى الملف.
            if old_slug and old_slug != self.slug:
                # slug تغيّر: حدّث الدور القديم إلى الجديد
                role = Role.objects.filter(slug=old_slug).first()
                if role:
                    updates = []
                    if role.slug != self.slug:
                        role.slug = self.slug
                        updates.append("slug")
                    if role.name != role_name:
                        role.name = role_name
                        updates.append("name")
                    if role.is_active != self.is_active:
                        role.is_active = self.is_active
                        updates.append("is_active")
                    if updates:
                        role.save(update_fields=updates)
                else:
                    # إن لم يوجد دور بالـ old_slug، أنشئ/حدّث بالـ slug الجديد
                    role, created = Role.objects.get_or_create(
                        slug=self.slug,
                        defaults={"name": role_name, "is_active": self.is_active},
                    )
                    if not created:
                        to_update = []
                        if role.name != role_name:
                            role.name = role_name
                            to_update.append("name")
                        if role.is_active != self.is_active:
                            role.is_active = self.is_active
                            to_update.append("is_active")
                        if to_update:
                            role.save(update_fields=to_update)
            else:
                role, created = Role.objects.get_or_create(
                    slug=self.slug,
                    defaults={"name": role_name, "is_active": self.is_active},
                )
                if not created:
                    to_update = []
                    if role.name != role_name:
                        role.name = role_name
                        to_update.append("name")
                    if role.is_active != self.is_active:
                        role.is_active = self.is_active
                        to_update.append("is_active")
                    if to_update:
                        role.save(update_fields=to_update)


class DepartmentMembership(models.Model):
    TEACHER = "teacher"
    OFFICER = "officer"
    ROLE_TYPE_CHOICES = [(TEACHER, "Teacher"), (OFFICER, "Officer")]

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="القسم",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dept_memberships",
        verbose_name="المعلم",
    )
    role_type = models.CharField("نوع التكليف", max_length=16, choices=ROLE_TYPE_CHOICES, default=TEACHER)

    class Meta:
        unique_together = [("department", "teacher")]  # إبقائها كما هي لتجنّب هجرة جديدة
        indexes = [
            models.Index(fields=["department"]),
            models.Index(fields=["teacher"]),
        ]
        verbose_name = "تكليف قسم"
        verbose_name_plural = "تكليفات الأقسام"

    def __str__(self):
        return f"{self.teacher} @ {self.department} ({self.role_type})"


# =========================
# مرجع أنواع التقارير الديناميكي
# =========================
class ReportType(models.Model):
    code = models.SlugField("الكود", max_length=40, unique=True)
    name = models.CharField("الاسم", max_length=120)
    description = models.TextField("الوصف", blank=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("أُنشئ", auto_now_add=True)
    updated_at = models.DateTimeField("تحديث", auto_now=True)

    class Meta:
        ordering = ("order", "name")
        verbose_name = "نوع تقرير"
        verbose_name_plural = "أنواع التقارير"

    def __str__(self) -> str:
        return self.name or self.code

    def save(self, *args, **kwargs):
        # تطبيع code إلى lowercase
        if self.code:
            self.code = self.code.strip().lower()
        super().save(*args, **kwargs)


# =========================
# نموذج التقرير العام
# =========================
class Report(models.Model):
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports",
        db_index=True,
        verbose_name="المعلم (حساب)",
    )

    # اسم المعلم وقت الإنشاء (للتجميد)
    teacher_name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="اسم المعلم (وقت الإنشاء)",
        help_text="يُحفظ هنا الاسم الظاهر بغض النظر عن تغيّر اسم الحساب لاحقًا.",
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

    # التصنيف ديناميكي عبر FK
    category = models.ForeignKey(
        "ReportType",
        on_delete=models.PROTECT,     # منع حذف النوع إن كان مستخدمًا
        null=True, blank=True,        # مؤقتًا لتسهيل الهجرة؛ يمكن جعلها إلزامية لاحقًا
        verbose_name="التصنيف",
        related_name="reports",
        db_index=True,
    )

    image1 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image2 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image3 = models.ImageField(upload_to="reports/", blank=True, null=True)
    image4 = models.ImageField(upload_to="reports/", blank=True, null=True)

    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["teacher", "category"]),
            models.Index(fields=["report_date"]),
        ]
        verbose_name = "تقرير"
        verbose_name_plural = "التقارير"

    def __str__(self):
        display_name = self.teacher_name.strip() if self.teacher_name else getattr(self.teacher, "name", "")
        cat = getattr(self.category, "name", "بدون تصنيف")
        return f"{self.title} - {cat} - {display_name} ({self.report_date})"

    @property
    def teacher_display_name(self) -> str:
        return (self.teacher_name or getattr(self.teacher, "name", "") or "").strip()

    def save(self, *args, **kwargs):
        # اليوم باللغة العربية
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
            try:
                self.day_name = days.get(self.report_date.isoweekday())
            except Exception:
                pass

        # تجميد اسم المعلّم وقت الإنشاء إن لم يُملأ
        if not self.teacher_name and getattr(self, "teacher_id", None):
            try:
                self.teacher_name = getattr(self.teacher, "name", "") or ""
            except Exception:
                pass

        super().save(*args, **kwargs)


# =========================
# منظومة التذاكر الموحّدة
# =========================
class Ticket(models.Model):
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

    # القسم ديناميكي كـ FK
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True, blank=True,          # مؤقتًا لتسهيل الهجرة؛ يمكن جعلها إلزامية لاحقًا
        related_name="tickets",
        verbose_name="القسم",
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
# نماذج تراثية (تبقى كما هي للاطلاع/أرشفة فقط)
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
