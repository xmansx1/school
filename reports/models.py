# reports/models.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from urllib.parse import quote
import os
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, FileExtensionValidator
from django.db import models, transaction
from django.db.models.signals import m2m_changed, post_migrate
from django.dispatch import receiver
from django.utils.text import slugify
from django.utils import timezone

# تخزين Cloudinary العام لملفات raw (PDF/DOCX/ZIP/صور)
from .storage import PublicRawMediaStorage

# =========================
# ثوابت عامة
# =========================
MANAGER_SLUG = "manager"
MANAGER_NAME = "الإدارة"
MANAGER_ROLE_LABEL = "المدير"


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
        # تطبيع slug
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
        # إن وُجد دور manager نربطه
        try:
            mgr = Role.objects.filter(slug=MANAGER_SLUG).first()
            if mgr:
                extra_fields.setdefault("role", mgr)
        except Exception:
            pass
        return self.create_user(phone, name, password, **extra_fields)


class Teacher(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField("رقم الجوال", max_length=20, unique=True)
    national_id = models.CharField("الهوية الوطنية", max_length=20, blank=True, null=True, unique=True)
    name = models.CharField("الاسم", max_length=150, db_index=True)

    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="الدور",
        related_name="users",
    )

    is_active = models.BooleanField("نشط", default=True)
    # يُحدَّث تلقائيًا حسب role.is_staff_by_default
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
        return getattr(self.role, "name", "-")

    def save(self, *args, **kwargs):
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

    # ربط القسم بأنواع التقارير
    reporttypes = models.ManyToManyField(
        "ReportType",
        blank=True,
        related_name="departments",
        verbose_name="أنواع التقارير المرتبطة",
        help_text="اختَر الأنواع التي يحق لمسؤولي هذا القسم الاطلاع عليها (تُزامَن تلقائيًا مع دور القسم).",
    )

    class Meta:
        ordering = ("id",)
        verbose_name = "قسم"
        verbose_name_plural = "الأقسام"

    def __str__(self):
        return self.name

    # ===== منع حذف قسم المدير الدائم =====
    def delete(self, *args, **kwargs):
        if self.slug == MANAGER_SLUG:
            raise ValidationError("لا يمكن حذف قسم المدير الدائم.")
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        """
        مزامنة جدول Role مع كل قسم:
        - ينشئ/يحدّث Role بنفس slug.
        - عند تغيير slug: دمج/إعادة تسمية.
        - قسم المدير MANAGER_SLUG: يُجبر خصائصه.
        """
        # تطبيع الحقول
        if self.slug:
            self.slug = self.slug.strip().lower()
        else:
            self.slug = slugify(self.name or "", allow_unicode=True)

        # إجبار خصائص قسم المدير
        if self.slug == MANAGER_SLUG:
            self.name = MANAGER_NAME
            self.role_label = MANAGER_ROLE_LABEL
            self.is_active = True

        if not self.role_label:
            self.role_label = self.name

        old_slug = None
        if self.pk:
            old_slug = Department.objects.filter(pk=self.pk).values_list("slug", flat=True).first()

        role_name = (self.role_label or self.name or "").strip()

        with transaction.atomic():
            # حماية قسم المدير من تغيير slug
            if old_slug == MANAGER_SLUG and self.slug != MANAGER_SLUG:
                self.slug = MANAGER_SLUG
                self.name = MANAGER_NAME
                self.role_label = MANAGER_ROLE_LABEL
                self.is_active = True

            super().save(*args, **kwargs)

            # --- مزامنة الدور المطابق ---
            _Role = Role
            _Teacher = Teacher

            if old_slug and old_slug != self.slug:
                old_role = _Role.objects.filter(slug=old_slug).first()
                if old_role:
                    target_role = _Role.objects.filter(slug=self.slug).first()
                    if target_role and target_role.pk != old_role.pk:
                        # ✅ دمج
                        to_update = []
                        if target_role.name != role_name:
                            target_role.name = role_name
                            to_update.append("name")
                        desired_is_staff = True if self.slug == MANAGER_SLUG else target_role.is_staff_by_default
                        desired_can_view_all = True if self.slug == MANAGER_SLUG else target_role.can_view_all_reports
                        if target_role.is_staff_by_default != desired_is_staff:
                            target_role.is_staff_by_default = desired_is_staff
                            to_update.append("is_staff_by_default")
                        if target_role.can_view_all_reports != desired_can_view_all:
                            target_role.can_view_all_reports = desired_can_view_all
                            to_update.append("can_view_all_reports")
                        if target_role.is_active != self.is_active:
                            target_role.is_active = self.is_active
                            to_update.append("is_active")
                        if to_update:
                            target_role.save(update_fields=to_update)

                        try:
                            target_role.allowed_reporttypes.add(
                                *old_role.allowed_reporttypes.values_list("pk", flat=True)
                            )
                        except Exception:
                            pass
                        try:
                            _Teacher.objects.filter(role=old_role).update(role=target_role)
                        except Exception:
                            pass
                        try:
                            old_role.delete()
                        except Exception:
                            pass
                    else:
                        # إعادة تسمية الدور القديم
                        updates = []
                        if old_role.slug != self.slug:
                            old_role.slug = self.slug
                            updates.append("slug")
                        if old_role.name != role_name:
                            old_role.name = role_name
                            updates.append("name")
                        if self.slug == MANAGER_SLUG:
                            if not old_role.is_staff_by_default:
                                old_role.is_staff_by_default = True
                                updates.append("is_staff_by_default")
                            if not old_role.can_view_all_reports:
                                old_role.can_view_all_reports = True
                                updates.append("can_view_all_reports")
                        if old_role.is_active != self.is_active:
                            old_role.is_active = self.is_active
                            updates.append("is_active")
                        if updates:
                            old_role.save(update_fields=updates)
                else:
                    # لا يوجد دور بـ old_slug → احصل/أنشئ دورًا بالـ slug الجديد
                    role, created = _Role.objects.get_or_create(
                        slug=self.slug,
                        defaults={
                            "name": role_name,
                            "is_active": self.is_active,
                            "is_staff_by_default": (self.slug == MANAGER_SLUG),
                            "can_view_all_reports": (self.slug == MANAGER_SLUG),
                        },
                    )
                    if not created:
                        to_update = []
                        if role.name != role_name:
                            role.name = role_name
                            to_update.append("name")
                        if role.is_active != self.is_active:
                            role.is_active = self.is_active
                            to_update.append("is_active")
                        if self.slug == MANAGER_SLUG:
                            if not role.is_staff_by_default:
                                role.is_staff_by_default = True
                                to_update.append("is_staff_by_default")
                            if not role.can_view_all_reports:
                                role.can_view_all_reports = True
                                to_update.append("can_view_all_reports")
                        if to_update:
                            role.save(update_fields=to_update)
            else:
                # لم يتغير slug → احصل/أنشئ وحدث الخصائص
                role, created = _Role.objects.get_or_create(
                    slug=self.slug,
                    defaults={
                        "name": role_name,
                        "is_active": self.is_active,
                        "is_staff_by_default": (self.slug == MANAGER_SLUG),
                        "can_view_all_reports": (self.slug == MANAGER_SLUG),
                    },
                )
                if not created:
                    to_update = []
                    if role.name != role_name:
                        role.name = role_name
                        to_update.append("name")
                    if role.is_active != self.is_active:
                        role.is_active = self.is_active
                        to_update.append("is_active")
                    if self.slug == MANAGER_SLUG:
                        if not role.is_staff_by_default:
                            role.is_staff_by_default = True
                            to_update.append("is_staff_by_default")
                        if not role.can_view_all_reports:
                            role.can_view_all_reports = True
                            to_update.append("can_view_all_reports")
                    if to_update:
                        role.save(update_fields=to_update)


def _sync_dept_reporttypes_to_role(dept: Department) -> None:
    """
    يقرأ اختيارات القسم (reporttypes) ويعكسها على الدور الموازي.
    - قسم المدير: يفوز بخاصية can_view_all_reports=True ولا يعتمد على القائمة.
    """
    try:
        role = Role.objects.filter(slug=dept.slug).first()
        if not role:
            return
        if dept.slug == MANAGER_SLUG:
            updates = []
            if not role.is_staff_by_default:
                role.is_staff_by_default = True
                updates.append("is_staff_by_default")
            if not role.can_view_all_reports:
                role.can_view_all_reports = True
                updates.append("can_view_all_reports")
            if updates:
                role.save(update_fields=updates)
            return
        role.allowed_reporttypes.set(dept.reporttypes.all())
    except Exception:
        # نتجاهل الأخطاء للحفاظ على استقرار عملية الحفظ
        pass


@receiver(m2m_changed, sender=Department.reporttypes.through)
def department_reporttypes_changed(sender, instance: Department, action, **kwargs):
    """مزامنة allowed_reporttypes للدور عند تعديل reporttypes في القسم."""
    if action in {"post_add", "post_remove", "post_clear"}:
        _sync_dept_reporttypes_to_role(instance)


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
        unique_together = [("department", "teacher")]
        indexes = [
            models.Index(fields=["department"]),
            models.Index(fields=["teacher"]),
        ]
        verbose_name = "تكليف قسم"
        verbose_name_plural = "تكليفات الأقسام"

    def __str__(self):
        return f"{self.teacher} @ {self.department} ({self.role_type})"

    # ===== ضمان: قسم المدير يقبل موظفين فقط =====
    def clean(self):
        super().clean()
        if getattr(self.department, "slug", "").lower() == MANAGER_SLUG and self.role_type != self.TEACHER:
            raise ValidationError("قسم المدير يقبل تكليف موظفين فقط (لا يوجد مسؤول قسم).")

    def save(self, *args, **kwargs):
        # إجبار الدور داخل القسم على TEACHER لقسم المدير
        if getattr(self.department, "slug", "").lower() == MANAGER_SLUG:
            self.role_type = self.TEACHER
        super().save(*args, **kwargs)


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
                1: "الاثنين", 2: "الثلاثاء", 3: "الأربعاء", 4: "الخميس",
                5: "الجمعة", 6: "السبت", 7: "الأحد"
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
MAX_ATTACHMENT_MB = 5
_MAX_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024


def validate_attachment_size(file_obj):
    """تحقق الحجم ≤ 5MB"""
    if getattr(file_obj, "size", 0) > _MAX_BYTES:
        raise ValidationError(f"حجم المرفق يتجاوز {MAX_ATTACHMENT_MB}MB.")


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
        null=True, blank=True,
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

    # ✅ مرفق يُرفع إلى Cloudinary كـ raw عام (type=upload)
    attachment = models.FileField(
        "مرفق",
        upload_to="tickets/",
        storage=PublicRawMediaStorage(),   # عام + raw
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "doc", "docx"]),
            validate_attachment_size,
        ],
        help_text=f"يسمح بـ PDF/صور/DOCX حتى {MAX_ATTACHMENT_MB}MB",
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

    # ======== خصائص مساعدة للقوالب ========
    @property
    def attachment_name_lower(self) -> str:
        return (getattr(self.attachment, "name", "") or "").lower()

    @property
    def attachment_is_image(self) -> bool:
        return self.attachment_name_lower.endswith((".jpg", ".jpeg", ".png", ".webp"))

    @property
    def attachment_is_pdf(self) -> bool:
        return self.attachment_name_lower.endswith(".pdf")

    @property
    def attachment_download_url(self) -> str:
        """
        • إذا كان التخزين Cloudinary → أدخل fl_attachment:<filename> داخل جزء /upload/.
        • غير Cloudinary → أضف Content-Disposition عبر query كحل احتياطي.
        """
        url = getattr(self.attachment, "url", "") or ""
        if not url:
            return ""

        filename = os.path.basename(getattr(self.attachment, "name", "")) or "download"

        # Cloudinary
        if "res.cloudinary.com" in url and "/upload/" in url:
            # مثال: /raw/upload/v123/... → /raw/upload/fl_attachment:my.pdf/v123/...
            safe_fn = quote(filename, safe="")
            return url.replace("/upload/", f"/upload/fl_attachment:{safe_fn}/")

        # غير Cloudinary: تلميح للتحميل
        sep = "&" if "?" in url else "?"
        dispo = quote(f"attachment; filename*=UTF-8''{filename}", safe="")
        return f"{url}{sep}response-content-disposition={dispo}"


class TicketNote(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="notes",
        verbose_name="التذكرة"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ticket_notes",
        verbose_name="كاتب الملاحظة"
    )
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
# نماذج تراثية (اختياري للأرشفة)
# =========================
REQUEST_DEPARTMENTS = [
    (MANAGER_SLUG, "المدير"),
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


# =========================
# إشارات تضمن القسم/الدور الدائم للمدير
# =========================
@receiver(post_migrate)
def ensure_manager_department_and_role(sender, **kwargs):
    """
    يضمن وجود قسم المدير ودوره بعد الهجرات:
    - Department(slug='manager', name='الإدارة', role_label='المدير', is_active=True)
    - Role(slug='manager', name='المدير', is_staff_by_default=True, can_view_all_reports=True, is_active=True)
    """
    try:
        with transaction.atomic():
            dep, _ = Department.objects.get_or_create(
                slug=MANAGER_SLUG,
                defaults={"name": MANAGER_NAME, "role_label": MANAGER_ROLE_LABEL, "is_active": True},
            )
            # إصلاح أي تغييرات غير مقصودة
            updates = []
            if dep.name != MANAGER_NAME:
                dep.name = MANAGER_NAME
                updates.append("name")
            if dep.role_label != MANAGER_ROLE_LABEL:
                dep.role_label = MANAGER_ROLE_LABEL
                updates.append("role_label")
            if not dep.is_active:
                dep.is_active = True
                updates.append("is_active")
            if updates:
                dep.save(update_fields=updates)

            role, created = Role.objects.get_or_create(
                slug=MANAGER_SLUG,
                defaults={
                    "name": MANAGER_ROLE_LABEL,
                    "is_staff_by_default": True,
                    "can_view_all_reports": True,
                    "is_active": True,
                },
            )
            if not created:
                r_upd = []
                if role.name != MANAGER_ROLE_LABEL:
                    role.name = MANAGER_ROLE_LABEL
                    r_upd.append("name")
                if not role.is_staff_by_default:
                    role.is_staff_by_default = True
                    r_upd.append("is_staff_by_default")
                if not role.can_view_all_reports:
                    role.can_view_all_reports = True
                    r_upd.append("can_view_all_reports")
                if not role.is_active:
                    role.is_active = True
                    r_upd.append("is_active")
                if r_upd:
                    role.save(update_fields=r_upd)
    except Exception:
        # لا نرفع خطأ أثناء post_migrate للحفاظ على استقرار الهجرات
        pass


# =========================
# الإشعارات
# =========================
class Notification(models.Model):
    title = models.CharField(max_length=120, blank=True, default="")
    message = models.TextField()
    is_important = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        Teacher, null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications_created"
    )

    class Meta:
        db_table = "reports_notification"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return self.title or (self.message[:30] + ("..." if len(self.message) > 30 else ""))


class NotificationRecipient(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="recipients")
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="notifications")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "reports_notification_recipient"
        indexes = [
            models.Index(fields=["teacher", "is_read", "-created_at"]),
        ]
        unique_together = (("notification", "teacher"),)

    def __str__(self):
        return f"{self.teacher} ← {self.notification}"
