# reports/views.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from .forms import (
    ReportForm,
    TeacherForm,
    TicketActionForm,
    TicketCreateForm,
    DepartmentForm,  # أضف هذا السطر
)
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

# ===== فورمات =====
from .forms import (
    ReportForm,
    TeacherForm,
    TicketActionForm,
    TicketCreateForm,
)
# إشعارات
try:
    from .forms import NotificationCreateForm  # type: ignore
except Exception:
    NotificationCreateForm = None  # type: ignore

# ===== موديلات =====
from .models import (
    Report,
    Teacher,
    Ticket,
    TicketNote,
    Role,
)

# موديلات الإشعارات
try:
    from .models import Notification, NotificationRecipient  # type: ignore
except Exception:
    Notification = None  # type: ignore
    NotificationRecipient = None  # type: ignore

# ===== صلاحيات =====
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
    from django.apps import apps as _apps
except Exception:  # pragma: no cover
    _apps = None  # type: ignore

try:
    from .permissions import is_officer  # type: ignore
except Exception:
    def is_officer(user) -> bool:
        try:
            if not getattr(user, "is_authenticated", False):
                return False
            from .models import DepartmentMembership  # import محلي
            role_type = getattr(DepartmentMembership, "OFFICER", "officer")
            return DepartmentMembership.objects.filter(
                teacher=user, role_type=role_type, department__is_active=True
            ).exists()
        except Exception:
            return False

DM_TEACHER = getattr(DepartmentMembership, "TEACHER", "teacher") if DepartmentMembership else "teacher"
DM_OFFICER = getattr(DepartmentMembership, "OFFICER", "officer") if DepartmentMembership else "officer"

def _is_staff(user) -> bool:
    return bool(user and user.is_authenticated and user.is_staff)

def _is_staff_or_officer(user) -> bool:
    """يسمح للموظّفين (is_staff) أو لمسؤولي الأقسام (Officer)."""
    return bool(getattr(user, "is_authenticated", False) and
                (getattr(user, "is_staff", False) or is_officer(user)))


# =========================
# أدوات مساعدة عامة
# =========================
def _safe_next_url(next_url: str | None) -> str | None:
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.scheme == "" and parsed.netloc == "":
        return next_url
    return None

def _role_display_map() -> dict:
    base = {"teacher": "المعلم", "manager": "المدير", "officer": "مسؤول قسم"}
    if Department is not None:
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
@role_required({"manager"})
@require_http_methods(["GET"])
def admin_reports(request: HttpRequest) -> HttpResponse:
    cats = allowed_categories_for(request.user)
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
        if cats and "all" not in cats:
            if category in cats:
                qs = qs.filter(category__code=category)
        else:
            qs = qs.filter(category__code=category)

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
    user = request.user
    if user.is_superuser:
        return redirect("reports:admin_reports")

    if not (Department is not None and DepartmentMembership is not None):
        messages.error(request, "صلاحيات المسؤول تتطلب تفعيل الأقسام وعضوياتها.")
        return redirect("reports:home")

    membership = (
        DepartmentMembership.objects.select_related("department")
        .filter(teacher=user, role_type=DM_OFFICER, department__is_active=True)
        .first()
    )

    if not (membership or is_officer(user)):
        messages.error(request, "لا تملك صلاحية مسؤول قسم.")
        return redirect("reports:home")

    dept = membership.department if membership else None

    allowed_cats_qs = None
    if dept is not None:
        allowed_cats_qs = getattr(dept, "reporttypes", None)
        allowed_cats_qs = (allowed_cats_qs.filter(is_active=True) if allowed_cats_qs is not None else None)

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
    try:
        r = _get_report_for_user_or_404(request.user, pk)  # 404 تلقائيًا خارج النطاق
        r.delete()
        messages.success(request, "🗑️ تم حذف التقرير بنجاح.")
    except Exception:
        messages.error(request, "تعذّر حذف التقرير أو لا تملك صلاحية لذلك.")
    return _safe_redirect(request, "reports:officer_reports")


# =========================
# الوصول إلى تقرير معيّن
# =========================
def _get_report_for_user_or_404(user, pk: int):
    qs = Report.objects.select_related("teacher", "category")

    if getattr(user, "is_staff", False):
        return get_object_or_404(qs, pk=pk)

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

    return get_object_or_404(qs, pk=pk, teacher=user)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_print(request: HttpRequest, pk: int) -> HttpResponse:
    r = _get_report_for_user_or_404(request.user, pk)

    dept_name = None
    try:
        cat = getattr(r, "category", None)
        if cat:
            if hasattr(cat, "department") and getattr(cat, "department", None):
                d = getattr(cat, "department")
                dept_name = getattr(d, "name", None) or getattr(d, "role_label", None) or getattr(d, "slug", None)

            if not dept_name and Department is not None:
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

            if not dept_name and Department is not None:
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
    resp["Content-Disposition"] = f'inline; filename="report-{r.pk}.pdf"'
    return resp


# =========================
# إدارة المعلّمين (مدير فقط)
# =========================
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def manage_teachers(request: HttpRequest) -> HttpResponse:
    term = (request.GET.get("q") or "").strip()

    if Department is not None:
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
                    form.save(commit=True)
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
                    form.save(commit=True)
                messages.success(request, "✏️ تم تحديث بيانات المستخدم بنجاح.")
                return redirect("reports:manage_teachers")
            except Exception:
                logger.exception("edit_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء التحديث.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
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
    view_mode = request.GET.get("view", "list")

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
    dept_obj = None
    dept_code = (code_or_pk or "").strip()

    if Department is not None:
        try:
            dept_obj = Department.objects.filter(slug__iexact=dept_code).first()
            if not dept_obj:
                try:
                    dept_obj = Department.objects.filter(pk=int(dept_code)).first()
                except (ValueError, TypeError):
                    dept_obj = None
        except Exception:
            dept_obj = None

        if dept_obj:
            dept_code = getattr(dept_obj, "slug", dept_code)

    dept_label = _arabic_label_for(dept_obj or dept_code)
    return dept_obj, dept_code, dept_label

def _members_for_department(dept_code: str):
    if not dept_code:
        return Teacher.objects.none()
    role_qs = Teacher.objects.filter(is_active=True, role__slug=dept_code)
    if DepartmentMembership is not None:
        member_ids = DepartmentMembership.objects.filter(
            department__slug=dept_code
        ).values_list("teacher_id", flat=True)
        qs = Teacher.objects.filter(is_active=True).filter(Q(role__slug=dept_code) | Q(id__in=member_ids)).distinct()
        return qs.order_by("name")
    return role_qs.order_by("name")

def _user_department_codes(user) -> list[str]:
    codes = set()
    try:
        role = getattr(user, "role", None)
        if role and getattr(role, "slug", None) and role.slug != "teacher":
            codes.add(role.slug)
    except Exception:
        pass

    if DepartmentMembership is not None:
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
    if Department is not None:
        qs = Department.objects.all().order_by("id")
        for d in qs:
            code = _dept_code_for(d)
            stats = _tickets_stats_for_department(code)
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
    if Department is not None and 'DepartmentForm' in globals() and (DepartmentForm is not None):
        return DepartmentForm
    if Department is not None:
        return _DepartmentForm
    return None


# ---- لوحة المدير المجمعة ----
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})
def admin_dashboard(request: HttpRequest) -> HttpResponse:
    ctx = {
        "reports_count": Report.objects.count(),
        "teachers_count": Teacher.objects.count(),
        "tickets_total": Ticket.objects.count(),
        "tickets_open": Ticket.objects.filter(status__in=["open", "in_progress"]).count(),
        "tickets_done": Ticket.objects.filter(status="done").count(),
        "tickets_rejected": Ticket.objects.filter(status="rejected").count(),
        "has_dept_model": Department is not None,
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
        {"departments": depts, "has_dept_model": Department is not None},
    )


# ---- الأقسام: إنشاء ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_create(request: HttpRequest) -> HttpResponse:
    FormCls = get_department_form()
    if not (Department is not None and FormCls is not None):
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
    if not (Department is not None and FormCls is not None):
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
    if Department is None:
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


# ---- مساعد لتعيين الدور عبر slug ----
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
    if Department is None:
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
        action = (request.POST.get("action") or "").strip()
        teacher = Teacher.objects.filter(pk=teacher_id).first()
        if not teacher:
            messages.error(request, "المعلّم غير موجود.")
            return redirect("reports:department_members", code=dept_code)

        if Department is not None and obj:
            try:
                with transaction.atomic():
                    ok = False
                    if action == "add":
                        ok = _dept_add_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم تكليف {teacher.name} في قسم «{dept_label}».")
                        else:
                            if not _assign_role_by_slug(teacher, dept_code):
                                messages.error(request, "تعذّر إسناد المعلّم — تحقّق من بنية DepartmentMembership/Role.")
                            else:
                                messages.warning(request, f"تم الإسناد عبر الدور (fallback). راجع بنية DepartmentMembership لاحقًا.")
                    elif action == "remove":
                        ok = _dept_remove_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم إلغاء تكليف {teacher.name}.")
                        else:
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
            "has_dept_model": Department is not None,
        },
    )


# ===== ReportType CRUD =====
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def reporttypes_list(request: HttpRequest) -> HttpResponse:
    if not (ReportType is not None):
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
    if ReportType is None:
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
    if ReportType is None:
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
    if ReportType is None:
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


# =========================
# الإشعارات (إرسال/استقبال)
# =========================

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")   # فتح للمسؤول Officer أيضًا
@require_http_methods(["GET", "POST"])
def notifications_create(request: HttpRequest) -> HttpResponse:
    if NotificationCreateForm is None:
        messages.error(request, "نموذج إنشاء الإشعار غير متوفر.")
        return redirect("reports:home")

    form = NotificationCreateForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    n = form.save(creator=request.user)
                messages.success(request, "✅ تم إرسال الإشعار.")
                return redirect("reports:notifications_sent")
            except Exception:
                logger.exception("notifications_create failed")
                messages.error(request, "تعذّر الإرسال. جرّب لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء.")
    return render(request, "reports/notifications_create.html", {"form": form, "title": "إنشاء إشعار"})


# حذف إشعار (مدير/سوبر/المرسِل)
@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["POST"])
def notification_delete(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None:
        messages.error(request, "نموذج الإشعار غير متاح.")
        return redirect("reports:notifications_sent")
    n = get_object_or_404(Notification, pk=pk)
    role_slug = getattr(getattr(request.user, "role", None), "slug", None)
    is_owner = getattr(n, "created_by_id", None) == request.user.id
    is_manager = bool(request.user.is_superuser or role_slug == "manager")
    if not (is_manager or is_owner):
        messages.error(request, "لا تملك صلاحية حذف هذا الإشعار.")
        return redirect("reports:notifications_sent")
    try:
        n.delete()
        messages.success(request, "🗑️ تم حذف الإشعار.")
    except Exception:
        logger.exception("notification_delete failed")
        messages.error(request, "تعذّر حذف الإشعار.")
    return redirect("reports:notifications_sent")


# تفاصيل إشعار: عنوان + محتوى + جدول المستلمين (اسم، دور، حالة)
def _recipient_is_read(rec) -> tuple[bool, str | None]:
    for flag in ("is_read", "read", "seen", "opened"):
        if hasattr(rec, flag):
            try:
                return (bool(getattr(rec, flag)), None)
            except Exception:
                pass
    for dt in ("read_at", "seen_at", "opened_at"):
        if hasattr(rec, dt):
            try:
                val = getattr(rec, dt)
                return (bool(val), getattr(val, "strftime", lambda fmt: None)("%Y-%m-%d %H:%M") if val else None)
            except Exception:
                pass
    if hasattr(rec, "status"):
        try:
            st = str(getattr(rec, "status") or "").lower()
            if st in {"read", "seen", "opened", "done"}:
                return (True, None)
        except Exception:
            pass
    return (False, None)

def _arabic_role_label(role_slug: str) -> str:
    return _role_display_map().get((role_slug or "").lower(), role_slug or "")

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notification_detail(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None:
        messages.error(request, "نموذج الإشعار غير متاح.")
        return redirect("reports:notifications_sent")

    n = get_object_or_404(Notification, pk=pk)

    role_slug = getattr(getattr(request.user, "role", None), "slug", None)
    if not (request.user.is_superuser or role_slug == "manager"):
        if getattr(n, "created_by_id", None) != request.user.id:
            messages.error(request, "لا تملك صلاحية عرض هذا الإشعار.")
            return redirect("reports:notifications_sent")

    body = (
        getattr(n, "message", None) or getattr(n, "body", None) or
        getattr(n, "content", None) or getattr(n, "text", None) or
        getattr(n, "details", None) or ""
    )

    recipients = []
    if NotificationRecipient is not None:
        # اكتشف اسم FK للإشعار
        notif_fk = None
        for f in NotificationRecipient._meta.get_fields():
            if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                notif_fk = f.name
                break

        # اسم حقل الشخص
        user_fk = None
        for cand in ("teacher", "user", "recipient"):
            if hasattr(NotificationRecipient, cand):
                user_fk = cand
                break

        if notif_fk:
            qs = NotificationRecipient.objects.filter(**{f"{notif_fk}": n})
            if user_fk:
                qs = qs.select_related(f"{user_fk}", f"{user_fk}__role")
            qs = qs.order_by("id")

            for r in qs:
                t = getattr(r, user_fk) if user_fk else None
                if not t:
                    continue
                name = getattr(t, "name", None) or getattr(t, "phone", None) or getattr(t, "username", None) or f"مستخدم #{getattr(t, 'pk', '')}"
                rslug = getattr(getattr(t, "role", None), "slug", "") or ""
                role_label = _arabic_role_label(rslug)
                is_read, read_at_str = _recipient_is_read(r)
                recipients.append({
                    "name": str(name),
                    "role": role_label,
                    "read": bool(is_read),
                    "read_at": read_at_str,
                })

    ctx = {
        "n": n,
        "body": body,
        "recipients": recipients,
    }
    return render(request, "reports/notification_detail.html", ctx)


# إشعاراتي (للمعلّم)
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_notifications(request: HttpRequest) -> HttpResponse:
    if NotificationRecipient is None:
        return render(request, "reports/my_notifications.html", {"page_obj": Paginator([], 12).get_page(1)})

    qs = (NotificationRecipient.objects
          .select_related("notification")
          .filter(teacher=request.user)
          .order_by("-created_at", "-id"))

    # إخفاء المنتهية بحسب الحقول المتاحة
    now = timezone.now()
    try:
        if Notification is not None and hasattr(Notification, "expires_at"):
            qs = qs.exclude(notification__expires_at__lt=now)
        elif Notification is not None and hasattr(Notification, "ends_at"):
            qs = qs.exclude(notification__ends_at__lt=now)
    except Exception:
        pass

    page = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    return render(request, "reports/my_notifications.html", {"page_obj": page})


# قائمة المرسلة (للمدير/المسؤول)
@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notifications_sent(request: HttpRequest) -> HttpResponse:
    if Notification is None:
        return render(request, "reports/notifications_sent.html", {"page_obj": Paginator([], 20).get_page(1), "stats": {}})

    qs = Notification.objects.all().order_by("-created_at", "-id")

    role_slug = getattr(getattr(request.user, "role", None), "slug", None)
    if role_slug and role_slug != "manager" and not request.user.is_superuser:
        qs = qs.filter(created_by=request.user)

    qs = qs.select_related("created_by")
    page = Paginator(qs, 20).get_page(request.GET.get("page") or 1)

    notif_ids = [n.id for n in page.object_list]
    stats: dict[int, dict] = {}

    # حساب read/total بمرونة على NotificationRecipient
    if NotificationRecipient is not None and notif_ids:
        notif_fk_name = None
        try:
            for f in NotificationRecipient._meta.get_fields():
                if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                    notif_fk_name = f.name
                    break
        except Exception:
            notif_fk_name = None

        if notif_fk_name:
            fields = {f.name for f in NotificationRecipient._meta.get_fields()}
            if "is_read" in fields:
                read_filter = Q(is_read=True)
            elif "read_at" in fields:
                read_filter = Q(read_at__isnull=False)
            elif "seen_at" in fields:
                read_filter = Q(seen_at__isnull=False)
            elif "status" in fields:
                read_filter = Q(status__in=["read", "seen", "opened", "done"])
            else:
                read_filter = Q(pk__in=[])

            rc = (NotificationRecipient.objects
                  .filter(**{f"{notif_fk_name}_id__in": notif_ids})
                  .values(f"{notif_fk_name}_id")
                  .annotate(total=Count("id"), read=Count("id", filter=read_filter)))
            for row in rc:
                stats[row[f"{notif_fk_name}_id"]] = {"total": row["total"], "read": row["read"]}

    # أسماء مستلمين مختصرة (نستعملها فقط لو أردتها لاحقًا؛ حالياً صفحة التفاصيل هي الأساس)
    rec_names_map: dict[int, list[str]] = {i: [] for i in notif_ids}

    def _name_of(person) -> str:
        return (getattr(person, "name", None) or
                getattr(person, "phone", None) or
                getattr(person, "username", None) or
                getattr(person, "national_id", None) or
                str(person))

    for n in page.object_list:
        names_set = set()
        try:
            rel = getattr(n, "recipients", None)
            if rel is not None:
                for t in rel.all()[:12]:
                    if t:
                        nm = _name_of(t)
                        if nm not in names_set:
                            names_set.add(nm)
        except Exception:
            pass
        rec_names_map[n.id] = list(names_set)

    remaining_ids = [nid for nid, arr in rec_names_map.items() if len(arr) < 5]
    if remaining_ids and NotificationRecipient is not None:
        notif_fk_name = None
        try:
            for f in NotificationRecipient._meta.get_fields():
                if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                    notif_fk_name = f.name
                    break
        except Exception:
            pass

        if notif_fk_name:
            thr_qs = NotificationRecipient.objects.filter(**{f"{notif_fk_name}_id__in": remaining_ids})
            for r in thr_qs:
                nid = getattr(r, f"{notif_fk_name}_id", None)
                if not nid:
                    continue
                person = (getattr(r, "teacher", None) or
                          getattr(r, "user", None) or
                          getattr(r, "recipient", None))
                if person:
                    nm = _name_of(person)
                    arr = rec_names_map.get(nid, [])
                    if nm and nm not in arr and len(arr) < 12:
                        arr.append(nm)
                        rec_names_map[nid] = arr

    for n in page.object_list:
        n.rec_names = rec_names_map.get(n.id, [])

    return render(request, "reports/notifications_sent.html", {"page_obj": page, "stats": stats})


# تعليم الإشعار كمقروء (حسب Recipient pk)
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notification_mark_read(request: HttpRequest, pk: int) -> HttpResponse:
    if NotificationRecipient is None:
        return redirect(request.POST.get("next") or "reports:my_notifications")
    item = get_object_or_404(NotificationRecipient, pk=pk, teacher=request.user)
    if not getattr(item, "is_read", False):
        if hasattr(item, "is_read"):
            item.is_read = True
        if hasattr(item, "read_at"):
            item.read_at = timezone.now()
        try:
            if hasattr(item, "is_read") and hasattr(item, "read_at"):
                item.save(update_fields=["is_read", "read_at"])
            else:
                item.save()
        except Exception:
            item.save()
    return redirect(request.POST.get("next") or "reports:my_notifications")


# تحديد الكل كمقروء
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notifications_mark_all_read(request: HttpRequest) -> HttpResponse:
    if NotificationRecipient is None:
        return redirect(request.POST.get("next") or "reports:my_notifications")
    qs = NotificationRecipient.objects.filter(teacher=request.user)
    try:
        if "is_read" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(is_read=False)
            qs.update(is_read=True, read_at=timezone.now() if hasattr(NotificationRecipient, "read_at") else None)
        elif "read_at" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(read_at__isnull=True)
            qs.update(read_at=timezone.now())
        else:
            pass
    except Exception:
        for x in qs:
            try:
                if hasattr(x, "is_read"):
                    x.is_read = True
                if hasattr(x, "read_at"):
                    x.read_at = timezone.now()
                x.save()
            except Exception:
                continue
    messages.success(request, "تم تحديد جميع الإشعارات كمقروءة.")
    return redirect(request.POST.get("next") or "reports:my_notifications")


# تعليم الإشعار كمقروء (حسب رقم الإشعار نفسه لا الـRecipient)
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notification_mark_read_by_notification(request: HttpRequest, pk: int) -> HttpResponse:
    if NotificationRecipient is None:
        return JsonResponse({"ok": False}, status=400)
    try:
        item = NotificationRecipient.objects.filter(
            notification_id=pk, teacher=request.user
        ).first()
        if item:
            if hasattr(item, "is_read") and not item.is_read:
                item.is_read = True
            if hasattr(item, "read_at") and getattr(item, "read_at", None) is None:
                item.read_at = timezone.now()
            try:
                if hasattr(item, "is_read") and hasattr(item, "read_at"):
                    item.save(update_fields=["is_read", "read_at"])
                else:
                    item.save()
            except Exception:
                item.save()
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False}, status=400)


# إبقاء المسار القديم للتوافق الخلفي: تحويل إلى صفحة الإنشاء
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
def send_notification(request: HttpRequest) -> HttpResponse:
    return redirect("reports:notifications_create")
