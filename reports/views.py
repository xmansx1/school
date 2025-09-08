# reports/views.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import traceback
from datetime import date
from urllib.parse import urlparse
from typing import Optional, Tuple

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    Count,
    Prefetch,
    Q,
    ManyToManyField,
    ForeignKey,
    OuterRef,
    Subquery,
    ProtectedError,
)
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .forms import (
    ReportForm,
    TeacherForm,
    TicketActionForm,
    TicketCreateForm,
)
from .models import (
    Report,
    Teacher,
    Ticket,
    TicketNote,
    Role,
)

from .permissions import allowed_categories_for, role_required, restrict_queryset_for_user

logger = logging.getLogger(__name__)

# ========= استيراد مرن للنماذج المرجعية =========
try:
    from .models import ReportType  # type: ignore
except Exception:  # pragma: no cover
    ReportType = None  # type: ignore

try:
    from .forms import ReportTypeForm  # type: ignore
except Exception:  # pragma: no cover
    ReportTypeForm = None  # type: ignore

HAS_RTYPE: bool = ReportType is not None

try:
    from .models import Department  # type: ignore
except Exception:  # pragma: no cover
    Department = None  # type: ignore

try:
    from .models import DepartmentMembership  # type: ignore
except Exception:  # pragma: no cover
    DepartmentMembership = None  # type: ignore

try:
    from .forms import DepartmentForm  # type: ignore
except Exception:  # pragma: no cover
    DepartmentForm = None  # pragma: no cover

HAS_DEPT_MODEL: bool = Department is not None

DM_TEACHER = getattr(DepartmentMembership, "TEACHER", "teacher") if DepartmentMembership else "teacher"
DM_OFFICER = getattr(DepartmentMembership, "OFFICER", "officer") if DepartmentMembership else "officer"

# ========= دعم اكتشاف Officer + فاحص صلاحيات موحّد =========
try:
    # إن كانت متوفرة في permissions سنستخدمها مباشرة
    from .permissions import is_officer  # type: ignore
except Exception:
    # بديل آمن إذا لم تتوفر الدالة
    def is_officer(user) -> bool:
        try:
            if not getattr(user, "is_authenticated", False):
                return False
            from .models import DepartmentMembership  # import محلي لتفادي الدورات
            role_type = getattr(DepartmentMembership, "OFFICER", "officer")
            return DepartmentMembership.objects.filter(
                teacher=user, role_type=role_type, department__is_active=True
            ).exists()
        except Exception:
            return False

def _is_staff(user) -> bool:
    return bool(user and user.is_authenticated and user.is_staff)

def _is_staff_or_officer(user) -> bool:
    """
    يسمح للموظّفين (is_staff) أو لمسؤولي الأقسام (Officer).
    لا يمنح Officer صلاحيات المدير إلا ضمن نطاق أنواعه عبر فلاتر الوصول في الدوال المساعدة.
    """
    return bool(getattr(user, "is_authenticated", False) and
                (getattr(user, "is_staff", False) or is_officer(user)))


# =========================
# أدوات مساعدة عامة
# =========================
def _safe_next_url(next_url: str | None) -> str | None:
    if not next_url:
        return None
    parsed = urlparse(next_url)
    # نسمح فقط بالمسارات النسبية (بدون دومين/بروتوكول)
    if parsed.scheme == "" and parsed.netloc == "":
        return next_url
    return None


def _role_display_map() -> dict:
    """
    خريطة عربية لعرض أسماء الأدوار باستخدام Department.role_label عند توفر Department.
    (Fallback آمن فقط إن لم يتوفر الموديل)
    """
    base = {"teacher": "المعلم", "manager": "المدير"}
    if HAS_DEPT_MODEL and Department is not None:
        try:
            for d in Department.objects.filter(is_active=True).only("slug", "role_label", "name"):
                base[d.slug] = d.role_label or d.name or d.slug
        except Exception:
            pass
    return base


def _safe_redirect(request: HttpRequest, fallback_name: str) -> HttpResponse:
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}):
        return redirect(nxt)
    return redirect(fallback_name)


def _parse_date_safe(value: str | None) -> date | None:
    if not value:
        return None
    return parse_date(value)


# =========================
# الدخول / الخروج
# =========================
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("reports:home")

    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=phone, password=password)
        if user is not None:
            login(request, user)
            next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
            return redirect(next_url or "reports:home")
        messages.error(request, "رقم الجوال أو كلمة المرور غير صحيحة")

    context = {"next": _safe_next_url(request.GET.get("next"))}
    return render(request, "reports/login.html", context)


@require_http_methods(["GET", "POST"])
def logout_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "تم تسجيل الخروج بنجاح.")
    return redirect("reports:login")


# =========================
# الرئيسية (لوحة المعلم)
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def home(request: HttpRequest) -> HttpResponse:
    stats = {"today_count": 0, "total_count": 0, "last_title": "—"}
    req_stats = {"open": 0, "in_progress": 0, "done": 0, "rejected": 0, "total": 0}

    try:
        my_qs = (
            Report.objects.filter(teacher=request.user)
            .only("id", "title", "report_date", "day_name", "beneficiaries_count")
        )
        today = timezone.localdate()
        stats["total_count"] = my_qs.count()
        stats["today_count"] = my_qs.filter(report_date=today).count()
        last_report = my_qs.order_by("-report_date", "-id").first()
        stats["last_title"] = (last_report.title if last_report else "—")
        recent_reports = list(my_qs.order_by("-report_date", "-id")[:5])

        my_tickets_qs = (
            Ticket.objects.filter(creator=request.user)
            .select_related("assignee", "department")
            .only("id", "title", "status", "department", "created_at", "assignee__name")
            .order_by("-created_at", "-id")
        )
        agg = my_tickets_qs.aggregate(
            open=Count("id", filter=Q(status="open")),
            in_progress=Count("id", filter=Q(status="in_progress")),
            done=Count("id", filter=Q(status="done")),
            rejected=Count("id", filter=Q(status="rejected")),
            total=Count("id"),
        )
        for k in req_stats.keys():
            req_stats[k] = int(agg.get(k) or 0)
        recent_tickets = list(my_tickets_qs[:5])

        return render(
            request,
            "reports/home.html",
            {
                "stats": stats,
                "recent_reports": recent_reports[:2],
                "req_stats": req_stats,
                "recent_tickets": recent_tickets[:2],
            },
        )
    except Exception:
        logger.exception("Home view failed")
        if settings.DEBUG or os.getenv("SHOW_ERRORS") == "1":
            html = "<h2>Home exception</h2><pre>{}</pre>".format(traceback.format_exc())
            return HttpResponse(html, status=500)
    # لا تكشف الاستثناء في الإنتاج
    return redirect("reports:home")


# =========================
# التقارير: إضافة/عرض/إدارة
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def add_report(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save(commit=False)
            report.teacher = request.user

            teacher_name_input = (request.POST.get("teacher_name") or "").strip()
            teacher_name_final = teacher_name_input or (getattr(request.user, "name", "") or "").strip()
            teacher_name_final = teacher_name_final[:120]
            if hasattr(report, "teacher_name"):
                report.teacher_name = teacher_name_final

            report.save()
            messages.success(request, "تم إضافة التقرير بنجاح ✅")
            return redirect("reports:my_reports")
        messages.error(request, "فضلاً تحقق من الحقول وأعد المحاولة.")
    else:
        form = ReportForm()

    return render(request, "reports/add_report.html", {"form": form})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_reports(request: HttpRequest) -> HttpResponse:
    qs = (
        Report.objects.select_related("teacher", "category")
        .filter(teacher=request.user)
        .order_by("-report_date", "-id")
    )
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)

    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 10)
    try:
        reports_page = paginator.page(page)
    except PageNotAnInteger:
        reports_page = paginator.page(1)
    except EmptyPage:
        reports_page = paginator.page(paginator.num_pages)

    return render(
        request,
        "reports/my_reports.html",
        {
            "reports": reports_page,
            "start_date": request.GET.get("start_date", ""),
            "end_date": request.GET.get("end_date", ""),
        },
    )


@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})              # المدير فقط (والسوبر يمر داخل الديكوريتر)
@require_http_methods(["GET"])
def admin_reports(request: HttpRequest) -> HttpResponse:
    # فلترة ديناميكية حسب صلاحيات الدور (from DB)
    cats = allowed_categories_for(request.user)  # {"activity", ...} أو {"all"}
    qs = Report.objects.select_related("teacher", "category").order_by("-report_date", "-id")
    qs = restrict_queryset_for_user(qs, request.user)

    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    teacher_name = (request.GET.get("teacher_name") or "").strip()
    category = (request.GET.get("category") or "").strip().lower()

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if teacher_name:
        for t in [t for t in teacher_name.split() if t]:
            qs = qs.filter(teacher_name__icontains=t)

    if category:
        # مسموح فقط إن كان ضمن الأنواع المصرّح بها
        if cats and "all" not in cats:
            if category in cats:
                qs = qs.filter(category__code=category)
        else:
            qs = qs.filter(category__code=category)

    # خيارات فلتر التصنيفات
    if HAS_RTYPE and ReportType is not None:
        rtypes_qs = ReportType.objects.all().order_by("order", "name")
        if cats and "all" not in cats:
            rtypes_qs = rtypes_qs.filter(code__in=list(cats))
        allowed_choices = [(rt.code, rt.name) for rt in rtypes_qs]
    else:
        allowed_choices = []

    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 20)
    try:
        reports_page = paginator.page(page)
    except PageNotAnInteger:
        reports_page = paginator.page(1)
    except EmptyPage:
        reports_page = paginator.page(paginator.num_pages)

    context = {
        "reports": reports_page,
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "teacher_name": teacher_name,
        "category": category if (not cats or "all" in cats or category in cats) else "",
        "categories": allowed_choices,
    }
    return render(request, "reports/admin_reports.html", context)


# =========================
# لوحة تقارير المسؤول (Officer)
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def officer_reports(request: HttpRequest) -> HttpResponse:
    """
    لوحة تقارير المسؤول:
    - تعتمد على عضوية DepartmentMembership.role_type = OFFICER.
    - المدير العام (superuser) يُحوّل للوحة المدير.
    - الأنواع المسموح بها = reporttypes المرتبطة بالقسم، مع fallback لأنواع الدور إن لزم.
    """
    user = request.user

    # المدير العام يستخدم لوحة المدير
    if user.is_superuser:
        return redirect("reports:admin_reports")

    if not (HAS_DEPT_MODEL and Department is not None and DepartmentMembership is not None):
        messages.error(request, "صلاحيات المسؤول تتطلب تفعيل الأقسام وعضوياتها.")
        return redirect("reports:home")

    # اكتشف عضوية المسؤول
    membership = (
        DepartmentMembership.objects.select_related("department")
        .filter(teacher=user, role_type=DM_OFFICER, department__is_active=True)
        .first()
    )
    if not membership:
        messages.error(request, "لا تملك صلاحية مسؤول قسم.")
        return redirect("reports:home")

    dept = membership.department  # القسم المسؤول عنه
    allowed_cats_qs = getattr(dept, "reporttypes", None)
    allowed_cats_qs = (allowed_cats_qs.filter(is_active=True) if allowed_cats_qs is not None else None)

    # احتياط: لو لا يوجد ربط أنواع، ارجع لأنواع الدور إن وُجدت
    role = getattr(user, "role", None)
    if (allowed_cats_qs is None) or (not allowed_cats_qs.exists()):
        if role is not None and hasattr(role, "allowed_reporttypes"):
            allowed_cats_qs = role.allowed_reporttypes.filter(is_active=True)
        else:
            allowed_cats_qs = ReportType.objects.none() if HAS_RTYPE and ReportType else None

    if allowed_cats_qs is None or not allowed_cats_qs.exists():
        messages.info(request, "لم يتم ربط قسمك بأي أنواع تقارير بعد.")
        return render(
            request,
            "reports/officer_reports.html",
            {
                "reports": [],
                "categories": [],
                "category": "",
                "teacher_name": "",
                "start_date": "",
                "end_date": "",
                "department": dept,
            },
        )

    # فلترة حسب المدخلات
    start_date = request.GET.get("start_date") or ""
    end_date = request.GET.get("end_date") or ""
    teacher_name = request.GET.get("teacher_name", "").strip()
    category = request.GET.get("category") or ""

    qs = Report.objects.select_related("teacher", "category").filter(category__in=allowed_cats_qs)

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if teacher_name:
        qs = qs.filter(Q(teacher__name__icontains=teacher_name) | Q(teacher_name__icontains=teacher_name))
    if category:
        # في officer_reports نستخدم pk للنوع في الفلتر الواجهـي
        qs = qs.filter(category_id=category)

    qs = qs.order_by("-report_date", "-created_at")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    categories_choices = [(str(c.pk), c.name) for c in allowed_cats_qs.order_by("order", "name")]

    return render(
        request,
        "reports/officer_reports.html",
        {
            "reports": page_obj,
            "categories": categories_choices,
            "category": category,
            "teacher_name": teacher_name,
            "start_date": start_date,
            "end_date": end_date,
            "department": dept,
        },
    )


# =========================
# حذف تقرير (لوحة المدير)
# =========================
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["POST"])
def admin_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    report = get_object_or_404(Report, pk=pk)
    report.delete()
    messages.success(request, "تم حذف التقرير بنجاح.")
    return _safe_redirect(request, "reports:admin_reports")


# =========================
# حذف تقرير (لوحة المسؤول Officer)
# =========================
@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["POST"])
def officer_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    """
    يسمح لمسؤول القسم بحذف تقرير داخل نطاق صلاحياته فقط.
    - التحقق من الوصول يتم عبر _get_report_for_user_or_404 (فلتر بالأنواع المسموحة أو تقاريره).
    - لا يمنح officer صلاحيات مدير؛ إنما يقيّده بتقاريره/أنواع قسمه.
    """
    try:
        r = _get_report_for_user_or_404(request.user, pk)  # 404 تلقائيًا خارج النطاق
        r.delete()
        messages.success(request, "🗑️ تم حذف التقرير بنجاح.")
    except Exception:
        messages.error(request, "تعذّر حذف التقرير أو لا تملك صلاحية لذلك.")
    return _safe_redirect(request, "reports:officer_reports")


# =========================
# الوصول إلى تقرير معيّن بحسب صلاحيات المستخدم (للطباعة/الـ PDF)
# =========================
def _get_report_for_user_or_404(user, pk: int):
    """
    يسمح للمدير/الموظف برؤية الكل، وللمستخدم العادي:
      - تقاريره الخاصة دائمًا
      - أو أي تقرير يقع ضمن الأنواع المسموح بها له (مسؤول قسم عبر Department/reporttypes).
    """
    qs = Report.objects.select_related("teacher", "category")

    # موظّف/مدير: يرى الكل
    if getattr(user, "is_staff", False):
        return get_object_or_404(qs, pk=pk)

    # فئات مسموح بها (مسؤول القسم، أو أدوار لها allowed_reporttypes)
    try:
        cats = allowed_categories_for(user) or set()
    except Exception:
        cats = set()

    if "all" in cats:
        return get_object_or_404(qs, pk=pk)

    if cats:
        return get_object_or_404(
            qs.filter(Q(teacher=user) | Q(category__code__in=list(cats))),
            pk=pk,
        )

    # دون صلاحيات إضافية: تقاريره فقط
    return get_object_or_404(qs, pk=pk, teacher=user)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_print(request: HttpRequest, pk: int) -> HttpResponse:
    # السماح بالطباعة بحسب صلاحيات الدور/القسم
    r = _get_report_for_user_or_404(request.user, pk)

    # توقيع = اسم القسم المرتبط بالتصنيف (إن وُجد)، وإلا "القسم"
    dept_name = None
    try:
        cat = getattr(r, "category", None)
        if cat:
            # 1) علاقة مباشرة FK: ReportType.department
            if hasattr(cat, "department") and getattr(cat, "department", None):
                d = getattr(cat, "department")
                dept_name = getattr(d, "name", None) or getattr(d, "role_label", None) or getattr(d, "slug", None)

            # 2) علاقة M2M: ReportType.departments (أو أسماء شائعة)
            if not dept_name and HAS_DEPT_MODEL and Department is not None:
                for rel_name in ("departments", "depts", "dept_list"):
                    if hasattr(cat, rel_name):
                        rel = getattr(cat, rel_name)
                        try:
                            first = rel.all().first() if hasattr(rel, "all") else None
                        except Exception:
                            first = None
                        if first:
                            dept_name = getattr(first, "name", None) or getattr(first, "role_label", None) or getattr(first, "slug", None)
                            if dept_name:
                                break

            # 3) بحث عكسي احتياطي: Department.reporttypes يحتوي هذا التصنيف
            if not dept_name and HAS_DEPT_MODEL and Department is not None:
                try:
                    d = Department.objects.filter(reporttypes=cat).only("name", "role_label", "slug").first()
                    if d:
                        dept_name = getattr(d, "name", None) or getattr(d, "role_label", None) or getattr(d, "slug", None)
                except Exception:
                    pass
    except Exception:
        pass

    signer_label = (dept_name or "القسم")

    return render(request, "reports/report_print.html", {"r": r, "signer_label": signer_label})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        from weasyprint import CSS, HTML
    except Exception:
        return HttpResponse("WeasyPrint غير مثبت. ثبّت الحزمة وشغّل مجددًا.", status=500)

    r = _get_report_for_user_or_404(request.user, pk)

    html = render_to_string("reports/report_print.html", {"r": r, "for_pdf": True}, request=request)
    css = CSS(string="@page { size: A4; margin: 14mm 12mm; }")
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf(stylesheets=[css])

    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="report-{r.pk}.pdf'
    return resp


# =========================
# إدارة المعلّمين (مدير فقط)
# =========================
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def manage_teachers(request: HttpRequest) -> HttpResponse:
    term = (request.GET.get("q") or "").strip()

    # جلب اسم القسم المطابق لرمز الدور (slug) عبر Subquery
    if HAS_DEPT_MODEL and Department is not None:
        dept_name_sq = Department.objects.filter(slug=OuterRef("role__slug")).values("name")[:1]
        qs = Teacher.objects.select_related("role").annotate(role_dept_name=Subquery(dept_name_sq)).order_by("-id")
    else:
        qs = Teacher.objects.select_related("role").order_by("-id")

    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(phone__icontains=term) | Q(national_id__icontains=term))

    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "reports/manage_teachers.html", {"teachers_page": page, "term": term})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def add_teacher(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = TeacherForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save(commit=True)  # الفورم ينشئ العضوية ويضبط الدور
                messages.success(request, "✅ تم إضافة المستخدم بنجاح.")
                next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                return redirect(next_url or "reports:manage_teachers")
            except IntegrityError:
                messages.error(request, "تعذّر الحفظ: قد يكون رقم الجوال أو الهوية مستخدمًا مسبقًا.")
            except Exception:
                logger.exception("add_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء الحفظ. جرّب لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherForm()
    return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def edit_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    teacher = get_object_or_404(Teacher, pk=pk)
    if request.method == "POST":
        form = TeacherForm(request.POST, instance=teacher)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save(commit=True)  # الفورم يحدّث الدور/العضوية
                messages.success(request, "✏️ تم تحديث بيانات المستخدم بنجاح.")
                return redirect("reports:manage_teachers")
            except Exception:
                logger.exception("edit_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء التحديث.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        # تهيئة مبدئية للقسم/الدور من العضوية أو الدور الحالي
        initial = {}
        memb = None
        if DepartmentMembership is not None:
            memb = teacher.dept_memberships.select_related("department").first()  # type: ignore[attr-defined]
        if memb:
            initial["department"] = getattr(memb.department, "slug", None)
            initial["membership_role"] = memb.role_type
        else:
            role_slug = getattr(getattr(teacher, "role", None), "slug", None)
            if role_slug:
                initial["department"] = role_slug
                initial["membership_role"] = DM_TEACHER
        form = TeacherForm(instance=teacher, initial=initial)

    return render(request, "reports/edit_teacher.html", {"form": form, "teacher": teacher, "title": "تعديل مستخدم"})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def delete_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    teacher = get_object_or_404(Teacher, pk=pk)
    try:
        with transaction.atomic():
            teacher.delete()
        messages.success(request, "🗑️ تم حذف المستخدم.")
    except Exception:
        logger.exception("delete_teacher failed")
        messages.error(request, "تعذّر حذف المستخدم. حاول لاحقًا.")
    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:manage_teachers")


# =========================
# التذاكر (Tickets)
# =========================
def _can_act(user, ticket: Ticket) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return (ticket.assignee_id is not None) and (ticket.assignee_id == user.id)


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def request_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = TicketCreateForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            t: Ticket = form.save(commit=False)
            t.creator = request.user
            t.status = Ticket.Status.OPEN
            t.save()
            messages.success(request, "✅ تم إرسال الطلب بنجاح.")
            return redirect("reports:my_requests")
        messages.error(request, "فضلاً تحقّق من الحقول.")
    else:
        form = TicketCreateForm(user=request.user)
    return render(request, "reports/request_create.html", {"form": form})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_requests(request: HttpRequest) -> HttpResponse:
    user = request.user

    notes_qs = (
        TicketNote.objects.filter(is_public=True)
        .select_related("author")
        .only("id", "body", "created_at", "author__name")
        .order_by("-created_at", "-id")
    )
    base_qs = (
        Ticket.objects.select_related("assignee", "department")
        .prefetch_related(Prefetch("notes", queryset=notes_qs, to_attr="pub_notes"))
        .only("id", "title", "status", "department", "created_at", "assignee__name")
        .filter(creator=user)
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        base_qs = base_qs.filter(Q(title__icontains=q) | Q(id__icontains=q) | Q(assignee__name__icontains=q))

    counts = dict(base_qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    stats = {
        "open": counts.get("open", 0),
        "in_progress": counts.get("in_progress", 0),
        "done": counts.get("done", 0),
        "rejected": counts.get("rejected", 0),
    }

    status = request.GET.get("status")
    qs = base_qs
    if status in {"open", "in_progress", "done", "rejected"}:
        qs = qs.filter(status=status)

    order = request.GET.get("order") or "-created_at"
    allowed_order = {"-created_at", "created_at", "-id", "id"}
    if order not in allowed_order:
        order = "-created_at"
    if order in {"created_at", "-created_at"}:
        qs = qs.order_by(order, "-id")
    else:
        qs = qs.order_by(order)

    page = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    view_mode = request.GET.get("view", "list")  # list | table

    return render(
        request,
        "reports/my_requests.html",
        {"tickets": page, "page_obj": page, "stats": stats, "view_mode": view_mode},
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def ticket_detail(request: HttpRequest, pk: int) -> HttpResponse:
    t: Ticket = get_object_or_404(
        Ticket.objects.select_related("creator", "assignee", "department").only(
            "id", "title", "body", "status", "department", "created_at",
            "creator__name", "assignee__name", "assignee_id", "creator_id"
        ),
        pk=pk,
    )

    is_owner = (t.creator_id == request.user.id)
    can_act = _can_act(request.user, t)

    if request.method == "POST":
        status_val = (request.POST.get("status") or "").strip()
        note_txt = (request.POST.get("note") or "").strip()
        changed = False

        if note_txt and (is_owner or can_act):
            try:
                TicketNote.objects.create(ticket=t, author=request.user, body=note_txt, is_public=True)
                changed = True
            except Exception:
                logger.exception("Failed to create note")
                messages.error(request, "تعذّر حفظ الملاحظة.")

        if status_val:
            if not can_act:
                messages.warning(request, "لا يمكنك تغيير حالة هذا الطلب. يمكنك فقط إضافة ملاحظة.")
            else:
                valid_statuses = dict(Ticket.Status.choices).keys()
                if status_val in valid_statuses and status_val != t.status:
                    old = t.status
                    t.status = status_val
                    try:
                        t.save(update_fields=["status"])
                    except Exception:
                        t.save()
                    changed = True
                    try:
                        TicketNote.objects.create(
                            ticket=t,
                            author=request.user,
                            body="تغيير الحالة: {} → {}".format(old, status_val),
                            is_public=True,
                        )
                    except Exception:
                        logger.exception("Failed to create system note")

        if changed:
            messages.success(request, "تم حفظ التغييرات.")
        else:
            messages.info(request, "لا يوجد تغييرات.")
        return redirect("reports:ticket_detail", pk=pk)

    notes_qs = (
        t.notes.select_related("author")
        .only("id", "body", "created_at", "author__name")
        .order_by("-created_at", "-id")
    )
    form = TicketActionForm(initial={"status": t.status}) if can_act else None

    ctx = {"t": t, "notes": notes_qs, "form": form, "can_act": can_act, "is_owner": is_owner}
    return render(request, "reports/ticket_detail.html", ctx)


@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def admin_request_update(request: HttpRequest, pk: int) -> HttpResponse:
    # نعيد استخدام نفس صفحة التفاصيل للمسؤول
    return ticket_detail(request, pk)


# ========= دعم الأقسام =========
def _dept_code_for(dept_obj_or_code) -> str:
    if hasattr(dept_obj_or_code, "slug") and getattr(dept_obj_or_code, "slug"):
        return getattr(dept_obj_or_code, "slug")
    if hasattr(dept_obj_or_code, "code") and getattr(dept_obj_or_code, "code"):
        return getattr(dept_obj_or_code, "code")
    return str(dept_obj_or_code or "").strip()


def _arabic_label_for(dept_obj_or_code) -> str:
    if hasattr(dept_obj_or_code, "name") and getattr(dept_obj_or_code, "name"):
        return dept_obj_or_code.name
    code = (
        getattr(dept_obj_or_code, "slug", None)
        or getattr(dept_obj_or_code, "code", None)
        or (dept_obj_or_code if isinstance(dept_obj_or_code, str) else "")
    )
    return _role_display_map().get(code, code or "—")


def _resolve_department_by_code_or_pk(code_or_pk: str) -> Tuple[Optional[object], str, str]:
    """
    يقبل slug أو pk رقمي. يتجنب استخدام lookups نصية على حقول رقمية.
    """
    dept_obj = None
    dept_code = (code_or_pk or "").strip()

    if HAS_DEPT_MODEL and Department is not None:
        try:
            # حاول بحسب الـ slug أولاً
            dept_obj = Department.objects.filter(slug__iexact=dept_code).first()
            if not dept_obj:
                # إن كان المدخل رقماً جرّب المطابقة كـ pk
                try:
                    dept_obj = Department.objects.filter(pk=int(dept_code)).first()
                except (ValueError, TypeError):
                    dept_obj = None
        except Exception:
            dept_obj = None

        if dept_obj:
            dept_code = getattr(dept_obj, "slug", dept_code)

    # عنوان عربي للسرد
    dept_label = _arabic_label_for(dept_obj or dept_code)
    return dept_obj, dept_code, dept_label


def _members_for_department(dept_code: str):
    """
    إرجاع أعضاء القسم عبر العضويات (DepartmentMembership) + الدور Teacher.role__slug.
    """
    if not dept_code:
        return Teacher.objects.none()
    # أساس: من يمتلك الدور بنفس slug
    role_qs = Teacher.objects.filter(is_active=True, role__slug=dept_code)
    # دمج مع العضويات
    if HAS_DEPT_MODEL and DepartmentMembership is not None:
        member_ids = DepartmentMembership.objects.filter(
            department__slug=dept_code
        ).values_list("teacher_id", flat=True)
        qs = Teacher.objects.filter(is_active=True).filter(Q(role__slug=dept_code) | Q(id__in=member_ids)).distinct()
        return qs.order_by("name")
    return role_qs.order_by("name")


def _user_department_codes(user) -> list[str]:
    """
    أكواد الأقسام الخاصة بالمستخدم:
    - من role.slug إن وُجد وكان ليس 'teacher'
    - ومن عضويات DepartmentMembership
    """
    codes = set()
    try:
        role = getattr(user, "role", None)
        if role and getattr(role, "slug", None) and role.slug != "teacher":
            codes.add(role.slug)
    except Exception:
        pass

    if HAS_DEPT_MODEL and DepartmentMembership is not None:
        try:
            mem_codes = DepartmentMembership.objects.filter(teacher=user)\
                          .values_list("department__slug", flat=True)
            for c in mem_codes:
                if c:
                    codes.add(c)
        except Exception:
            logger.exception("Failed to fetch user department codes")

    return list(codes)


def _tickets_stats_for_department(dept_code: str) -> dict:
    qs = Ticket.objects.filter(department__slug=dept_code)
    return {
        "open": qs.filter(status="open").count(),
        "in_progress": qs.filter(status="in_progress").count(),
        "done": qs.filter(status="done").count(),
    }


def _all_departments():
    items = []
    if HAS_DEPT_MODEL and Department is not None:
        qs = Department.objects.all().order_by("id")
        for d in qs:
            code = _dept_code_for(d)
            stats = _tickets_stats_for_department(code)

            # احسب عدد الأعضاء عبر العضويات + الدور بدون تكرار
            role_ids = set(Teacher.objects.filter(role__slug=code, is_active=True).values_list("id", flat=True))
            member_ids = set()
            if DepartmentMembership is not None:
                member_ids = set(DepartmentMembership.objects.filter(department=d).values_list("teacher_id", flat=True))
            members_count = len(role_ids | member_ids)

            items.append(
                {
                    "pk": d.pk,
                    "code": code,
                    "name": _arabic_label_for(d),
                    "is_active": getattr(d, "is_active", True),
                    "members_count": members_count,
                    "stats": stats,
                }
            )
    else:
        items = []
    return items


# ---- نموذج قسم احتياطي + مزوّد موحّد ----
class _DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields: list[str] = []
        if model is not None:
            for fname in ("name", "slug", "role_label", "is_active"):
                if hasattr(model, fname):
                    fields.append(fname)

    def clean(self):
        cleaned = super().clean()
        return cleaned


def get_department_form():
    if DepartmentForm is not None and Department is not None:
        return DepartmentForm
    if Department is not None:
        return _DepartmentForm
    return None


# ---- لوحة المدير المجمعة ----
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
def admin_dashboard(request: HttpRequest) -> HttpResponse:
    ctx = {
        "reports_count": Report.objects.count(),
        "teachers_count": Teacher.objects.count(),
        "tickets_total": Ticket.objects.count(),
        "tickets_open": Ticket.objects.filter(status__in=["open", "in_progress"]).count(),
        "tickets_done": Ticket.objects.filter(status="done").count(),
        "tickets_rejected": Ticket.objects.filter(status="rejected").count(),
        "has_dept_model": HAS_DEPT_MODEL,
    }

    has_reporttype = False
    reporttypes_count = 0
    try:
        from .models import ReportType  # type: ignore
        has_reporttype = True
        if hasattr(ReportType, "is_active"):
            reporttypes_count = ReportType.objects.filter(is_active=True).count()
        else:
            reporttypes_count = ReportType.objects.count()
    except Exception:
        pass

    ctx.update({
        "has_reporttype": has_reporttype,
        "reporttypes_count": reporttypes_count,
    })

    return render(request, "reports/admin_dashboard.html", ctx)


# ---- الأقسام: عرض ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def departments_list(request: HttpRequest) -> HttpResponse:
    depts = _all_departments()
    return render(
        request,
        "reports/departments_list.html",
        {"departments": depts, "has_dept_model": HAS_DEPT_MODEL},
    )


# ---- الأقسام: إنشاء ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_create(request: HttpRequest) -> HttpResponse:
    FormCls = get_department_form()
    if not (HAS_DEPT_MODEL and Department is not None and FormCls is not None):
        messages.error(request, "إنشاء الأقسام يتطلب تفعيل موديل Department.")
        return redirect("reports:departments_list")

    form = FormCls(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "✅ تم إنشاء القسم.")
            return redirect("reports:departments_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/department_form.html", {"form": form, "mode": "create"})


# ---- الأقسام: تحديث (بالـ pk) ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_update(request: HttpRequest, pk: int) -> HttpResponse:
    FormCls = get_department_form()
    if not (HAS_DEPT_MODEL and Department is not None and FormCls is not None):
        messages.error(request, "نموذج الأقسام غير مُعد بعد.")
        return redirect("reports:departments_list")
    dep = get_object_or_404(Department, pk=pk)  # type: ignore[arg-type]
    form = FormCls(request.POST or None, instance=dep)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "✏️ تم تحديث بيانات القسم.")
        return redirect("reports:departments_list")
    return render(request, "reports/department_form.html", {"form": form, "title": "تعديل قسم", "dep": dep})


# ---- الأقسام: تعديل (بالـ slug/code أو pk) ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_edit(request: HttpRequest, code: str) -> HttpResponse:
    if not (HAS_DEPT_MODEL and Department is not None):
        messages.error(request, "تعديل الأقسام غير متاح بدون موديل Department.")
        return redirect("reports:departments_list")

    obj, _, label = _resolve_department_by_code_or_pk(code)
    if not obj:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    FormCls = get_department_form()
    if not FormCls:
        messages.error(request, "DepartmentForm غير متاح.")
        return redirect("reports:departments_list")

    form = FormCls(request.POST or None, instance=obj)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"✏️ تم تحديث قسم «{label}».")
            return redirect("reports:departments_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/department_form.html", {"form": form, "mode": "edit", "department": obj})


# ---- مساعد لتعيين الدور عبر slug (fallback عند عدم نجاح العضويات) ----
def _assign_role_by_slug(teacher: Teacher, slug: str) -> bool:
    role_obj = Role.objects.filter(slug=slug).first()
    if not role_obj:
        return False
    teacher.role = role_obj
    try:
        teacher.save(update_fields=["role"])
    except Exception:
        teacher.save()
    return True


# ---- الأقسام: حذف ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def department_delete(request: HttpRequest, code: str) -> HttpResponse:
    if not (HAS_DEPT_MODEL and Department is not None):
        messages.error(request, "حذف الأقسام غير متاح بدون موديل Department.")
        return redirect("reports:departments_list")

    obj, _, label = _resolve_department_by_code_or_pk(code)
    if not obj:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    try:
        obj.delete()
        messages.success(request, f"🗑️ تم حذف قسم «{label}».")
    except ProtectedError:
        messages.error(request, f"لا يمكن حذف «{label}» لوجود سجلات مرتبطة به. عطّل القسم أو احذف السجلات المرتبطة أولاً.")
    except Exception:
        logger.exception("department_delete failed")
        messages.error(request, "تعذّر حذف القسم.")
    return redirect("reports:departments_list")


# ---- دعم m2m و through detection (احتياطي) ----
def _dept_m2m_field_name_to_teacher(dep_obj) -> str | None:
    try:
        if dep_obj is None:
            return None
        for f in dep_obj._meta.get_fields():
            if isinstance(f, ManyToManyField) and getattr(f.remote_field, "model", None) is Teacher:
                return f.name
    except Exception:
        logger.exception("Failed to detect forward M2M Department→Teacher")
    return None


def _deptmember_field_names() -> tuple[str | None, str | None]:
    dep_field = tea_field = None
    try:
        if DepartmentMembership is None:
            return (None, None)

        for f in DepartmentMembership._meta.get_fields():
            if isinstance(f, ForeignKey):
                if getattr(f.remote_field, "model", None) is Department and dep_field is None:
                    dep_field = f.name
                elif getattr(f.remote_field, "model", None) is Teacher and tea_field is None:
                    tea_field = f.name
            if dep_field and tea_field:
                break

        if dep_field is None:
            for n in ("department", "dept", "dept_fk"):
                if hasattr(DepartmentMembership, n):
                    dep_field = n
                    break
        if tea_field is None:
            for n in ("teacher", "member", "user", "teacher_fk"):
                if hasattr(DepartmentMembership, n):
                    tea_field = n
                    break
    except Exception:
        logger.exception("Failed to detect DepartmentMembership FKs")

    return (dep_field, tea_field)


def _dept_add_member(dep, teacher: Teacher) -> bool:
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).add(teacher)
            return True
    except Exception:
        logger.exception("Add via Department M2M failed")

    try:
        if DepartmentMembership is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                DepartmentMembership.objects.get_or_create(**kwargs)
                return True
    except Exception:
        logger.exception("Add via DepartmentMembership failed")

    return False


def _dept_remove_member(dep, teacher: Teacher) -> bool:
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).remove(teacher)
            return True
    except Exception:
        logger.exception("Remove via Department M2M failed")

    try:
        if DepartmentMembership is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                deleted, _ = DepartmentMembership.objects.filter(**kwargs).delete()
                return deleted > 0
    except Exception:
        logger.exception("Remove via DepartmentMembership failed")

    return False


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_members(request: HttpRequest, code: str | int) -> HttpResponse:
    obj, dept_code, dept_label = _resolve_department_by_code_or_pk(str(code))
    if not dept_code:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    if request.method == "POST":
        teacher_id = request.POST.get("teacher_id")
        action = (request.POST.get("action") or "").strip()  # add/remove
        teacher = Teacher.objects.filter(pk=teacher_id).first()
        if not teacher:
            messages.error(request, "المعلّم غير موجود.")
            return redirect("reports:department_members", code=dept_code)

        if HAS_DEPT_MODEL and Department is not None and obj:
            try:
                with transaction.atomic():
                    ok = False
                    if action == "add":
                        ok = _dept_add_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم تكليف {teacher.name} في قسم «{dept_label}».")
                        else:
                            # fallback: تعيين الدور حسب slug القسم
                            if not _assign_role_by_slug(teacher, dept_code):
                                messages.error(request, "تعذّر إسناد المعلّم — تحقّق من بنية DepartmentMembership/Role.")
                            else:
                                messages.warning(request, f"تم الإسناد عبر الدور (fallback). راجع بنية DepartmentMembership لاحقًا.")
                    elif action == "remove":
                        ok = _dept_remove_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم إلغاء تكليف {teacher.name}.")
                        else:
                            # fallback: إن كان دوره نفس القسم أعده teacher
                            if getattr(getattr(teacher, "role", None), "slug", None) == dept_code:
                                if not _assign_role_by_slug(teacher, "teacher"):
                                    messages.error(request, "تعذّر الإلغاء (الدور).")
                                else:
                                    messages.warning(request, "تم الإلغاء عبر الدور (fallback).")
                            else:
                                messages.error(request, "تعذّر إلغاء التكليف — تحقق من بنية العلاقات.")
                    else:
                        messages.error(request, "إجراء غير معروف.")
            except Exception:
                logger.exception("department_members mutation failed")
                messages.error(request, "حدث خطأ أثناء حفظ التغييرات.")
        else:
            messages.error(request, "إدارة الأعضاء تتطلب تفعيل موديل Department.")
            return redirect("reports:departments_list")

        return redirect("reports:department_members", code=dept_code)

    members_qs = _members_for_department(dept_code)
    all_teachers = Teacher.objects.filter(is_active=True).order_by("name")
    available = (
        all_teachers.exclude(id__in=members_qs.values_list("id", flat=True))
        if hasattr(members_qs, "values_list")
        else all_teachers
    )

    return render(
        request,
        "reports/department_members.html",
        {
            "department": obj if obj else {"code": dept_code, "name": dept_label},
            "dept_code": dept_code,
            "dept_label": dept_label,
            "members": members_qs,
            "all_teachers": available,
            "has_dept_model": HAS_DEPT_MODEL,
        },
    )


# ===== ReportType CRUD =====
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def reporttypes_list(request: HttpRequest) -> HttpResponse:
    if not (HAS_RTYPE and ReportType is not None):
        messages.error(request, "إدارة الأنواع تتطلب تفعيل موديل ReportType وتشغيل الهجرات.")
        return render(request, "reports/reporttypes_list.html", {"items": [], "db_backed": False})

    qs = ReportType.objects.all().order_by("order", "name")
    items = []
    for rt in qs:
        cnt = Report.objects.filter(category__code=rt.code).count()
        items.append({"obj": rt, "code": rt.code, "name": rt.name, "is_active": rt.is_active, "order": rt.order, "count": cnt})
    return render(request, "reports/reporttypes_list.html", {"items": items, "db_backed": True})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def reporttype_create(request: HttpRequest) -> HttpResponse:
    if not (HAS_RTYPE and ReportType is not None):
        messages.error(request, "إنشاء الأنواع يتطلب تفعيل موديل ReportType.")
        return redirect("reports:reporttypes_list")

    if ReportTypeForm is None:
        class _RTForm(forms.ModelForm):
            class Meta:
                model = ReportType
                fields = ("name", "code", "description", "order", "is_active")
        FormCls = _RTForm
    else:
        FormCls = ReportTypeForm

    form = FormCls(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "✅ تم إضافة نوع التقرير.")
            return redirect("reports:reporttypes_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/reporttype_form.html", {"form": form, "mode": "create"})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def reporttype_update(request: HttpRequest, pk: int) -> HttpResponse:
    if not (HAS_RTYPE and ReportType is not None):
        messages.error(request, "تعديل الأنواع يتطلب تفعيل موديل ReportType.")
        return redirect("reports:reporttypes_list")

    obj = get_object_or_404(ReportType, pk=pk)

    if ReportTypeForm is None:
        class _RTForm(forms.ModelForm):
            class Meta:
                model = ReportType
                fields = ("name", "code", "description", "order", "is_active")
        FormCls = _RTForm
    else:
        FormCls = ReportTypeForm

    form = FormCls(request.POST or None, instance=obj)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "✏️ تم تعديل نوع التقرير.")
            return redirect("reports:reporttypes_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/reporttype_form.html", {"form": form, "mode": "edit", "obj": obj})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def reporttype_delete(request: HttpRequest, pk: int) -> HttpResponse:
    if not (HAS_RTYPE and ReportType is not None):
        messages.error(request, "حذف الأنواع يتطلب تفعيل موديل ReportType.")
        return redirect("reports:reporttypes_list")

    obj = get_object_or_404(ReportType, pk=pk)
    used = Report.objects.filter(category__code=obj.code).count()
    if used > 0:
        messages.error(request, f"لا يمكن حذف «{obj.name}» لوجود {used} تقرير مرتبط. يمكنك تعطيله بدلًا من الحذف.")
        return redirect("reports:reporttypes_list")

    try:
        obj.delete()
        messages.success(request, f"🗑️ تم حذف «{obj.name}».")
    except Exception:
        logger.exception("reporttype_delete failed")
        messages.error(request, "تعذّر حذف نوع التقرير.")

    return redirect("reports:reporttypes_list")


# =========================
# واجهة برمجية مساعدة
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def api_department_members(request: HttpRequest) -> HttpResponse:
    dept = (request.GET.get("department") or "").strip()
    if not dept:
        return JsonResponse({"results": []})

    users = _members_for_department(dept).values("id", "name")
    return JsonResponse({"results": list(users)})


# =========================
# صناديق التذاكر بحسب القسم/المُعيّن
# =========================
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET"])
def tickets_inbox(request: HttpRequest) -> HttpResponse:
    qs = Ticket.objects.select_related("creator", "assignee", "department").order_by("-created_at")

    # ليس مديرًا؟ اعرض تذاكر معيّنة له أو ضمن أقسامه
    is_manager = bool(getattr(getattr(request.user, "role", None), "slug", None) == "manager")
    if not is_manager:
        user_codes = _user_department_codes(request.user)
        qs = qs.filter(Q(assignee=request.user) | Q(department__slug__in=user_codes))

    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    mine = request.GET.get("mine") == "1"

    if status:
        qs = qs.filter(status=status)
    if mine:
        qs = qs.filter(assignee=request.user)
    if q:
        for kw in q.split():
            qs = qs.filter(Q(title__icontains=kw) | Q(body__icontains=kw))

    ctx = {
        "tickets": qs[:200],
        "status": status,
        "q": q,
        "mine": mine,
        "status_choices": Ticket.Status.choices,
    }
    return render(request, "reports/tickets_inbox.html", ctx)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def assigned_to_me(request: HttpRequest) -> HttpResponse:
    user = request.user
    user_codes = _user_department_codes(user)

    qs = Ticket.objects.select_related("creator", "assignee", "department").filter(
        Q(assignee=user) | Q(assignee__isnull=True, department__slug__in=user_codes)
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(creator__name__icontains=q) | Q(id__icontains=q))

    status = request.GET.get("status")
    if status in {"open", "in_progress", "done", "rejected"}:
        qs = qs.filter(status=status)

    order = request.GET.get("order") or "-created_at"
    allowed_order = {"-created_at", "created_at", "-id", "id"}
    if order not in allowed_order:
        order = "-created_at"
    if order in {"created_at", "-created_at"}:
        qs = qs.order_by(order, "-id")
    else:
        qs = qs.order_by(order)

    raw_counts = dict(qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    stats = {
        "open": raw_counts.get("open", 0),
        "in_progress": raw_counts.get("in_progress", 0),
        "done": raw_counts.get("done", 0),
        "rejected": raw_counts.get("rejected", 0),
    }

    page_obj = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    view_mode = request.GET.get("view", "list")

    return render(request, "reports/assigned_to_me.html", {"page_obj": page_obj, "stats": stats, "view_mode": view_mode})


# =========================
# تقارير: تعديل/حذف للمستخدم الحالي
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def edit_my_report(request: HttpRequest, pk: int) -> HttpResponse:
    r = get_object_or_404(Report, pk=pk, teacher=request.user)

    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES, instance=r)
        if form.is_valid():
            form.save()
            messages.success(request, "✏️ تم تحديث التقرير بنجاح.")
            nxt = request.POST.get("next") or request.GET.get("next")
            return redirect(nxt or "reports:my_reports")
        messages.error(request, "تحقّق من الحقول.")
    else:
        form = ReportForm(instance=r)

    return render(request, "reports/edit_report.html", {"form": form, "report": r})


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def delete_my_report(request: HttpRequest, pk: int) -> HttpResponse:
    r = get_object_or_404(Report, pk=pk, teacher=request.user)
    r.delete()
    messages.success(request, "🗑️ تم حذف التقرير.")
    nxt = request.POST.get("next") or request.GET.get("next")
    return redirect(nxt or "reports:my_reports")
