# reports/views.py
from __future__ import annotations

import logging
import os
import traceback
from datetime import date
from urllib.parse import urlparse
from typing import Optional, Tuple
from django.db.models import ManyToManyField, ForeignKey

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch, Q
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
    # TicketNoteForm
)
from .models import (
    Report,
    Teacher,
    Ticket,
    TicketNote,
    ROLE_CHOICES,
)
from .permissions import allowed_categories_for, role_required

logger = logging.getLogger(__name__)

# ========= أقسام (Imports اختيارية، كل واحد لوحده) =========
# لا تجعل غياب DepartmentMember يعطّل Department

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
    from .models import DepartmentMember  # type: ignore
except Exception:  # pragma: no cover
    DepartmentMember = None  # type: ignore

try:
    from .forms import DepartmentForm  # type: ignore
except Exception:  # pragma: no cover
    DepartmentForm = None  # type: ignore

HAS_DEPT_MODEL: bool = Department is not None

# ========= تسميات عربية للأدوار/الأقسام =========
ROLE_LABELS = {
    "teacher": "المعلمين",
    "manager": "المدير",
    "activity_officer": "النشاط الطلابي",
    "volunteer_officer": "التطوع",
    "affairs_officer": "شؤون الطلاب",
    "admin_officer": "الشؤون الإدارية",
}

# =========================
# أدوات مساعدة عامة
# =========================
def _safe_next_url(next_url: str | None) -> str | None:
    """يمنع إعادة التوجيه لخارج الموقع (open redirect)."""
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.scheme == "" and parsed.netloc == "":
        return next_url
    return None

def _role_display_map() -> dict:
    """
    خريطة عربية لعرض أسماء الأدوار:
    - teacher/manager ثابتتان
    - بقية الأدوار تُقرأ من Department.role_label (أو name)
    """
    base = {"teacher": "المعلم", "manager": "المدير"}
    if HAS_DEPT_MODEL and Department is not None:
        try:
            for d in Department.objects.filter(is_active=True).only("slug", "role_label", "name"):
                base[d.slug] = d.role_label or d.name or d.slug
        except Exception:
            pass
    else:
        # fallback قديم إن كنت تستخدم ROLE_CHOICES
        try:
            base.update(dict(ROLE_CHOICES))
        except Exception:
            pass
    return base


def _safe_redirect(request: HttpRequest, fallback_name: str) -> HttpResponse:
    """Redirect آمن إلى ?next= لو داخلي وإلا إلى fallback."""
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}):
        return redirect(nxt)
    return redirect(fallback_name)


def _parse_date_safe(value: str | None) -> date | None:
    if not value:
        return None
    return parse_date(value)


def _is_staff(user) -> bool:
    return bool(user and user.is_authenticated and user.is_staff)


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
    req_stats = {"new": 0, "in_progress": 0, "done": 0, "rejected": 0, "total": 0}

    try:
        # تقارير المعلم
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

        # طلبات أنشأها المستخدم
        my_tickets_qs = (
            Ticket.objects.filter(creator=request.user)
            .select_related("assignee")
            .only("id", "title", "status", "department", "created_at", "assignee__name")
            .order_by("-created_at", "-id")
        )
        agg = my_tickets_qs.aggregate(
            new=Count("id", filter=Q(status__in=["new", "open"])),
            in_progress=Count("id", filter=Q(status__in=["in_progress", "pending"])),
            done=Count("id", filter=Q(status="done")),
            rejected=Count("id", filter=Q(status__in=["rejected", "cancelled"])),
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
                "recent_reports": recent_reports[:2],     # آخر 2 للعرض السريع
                "req_stats": req_stats,
                "recent_tickets": recent_tickets[:2],     # آخر 2 للعرض السريع
            },
        )
    except Exception:
        logger.exception("Home view failed")
        if settings.DEBUG or os.getenv("SHOW_ERRORS") == "1":
            html = "<h2>Home exception</h2><pre>{}</pre>".format(traceback.format_exc())
            return HttpResponse(html, status=500)
        raise


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
        Report.objects.select_related("teacher")
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
@require_http_methods(["GET"])
def admin_reports(request: HttpRequest) -> HttpResponse:
    cats = allowed_categories_for(request.user)
    qs = Report.objects.select_related("teacher").order_by("-report_date", "-id")

    if cats and "all" not in cats:
        allowed = [c for c, _ in getattr(Report, "Category").choices if c in cats]
        qs = qs.filter(category__in=allowed)

    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    teacher_name = (request.GET.get("teacher_name") or "").strip()
    category = (request.GET.get("category") or "").strip()

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)

    if teacher_name:
        for t in [t for t in teacher_name.split() if t]:
            qs = qs.filter(teacher_name__icontains=t)

    if category:
        if ("all" in cats and category in dict(getattr(Report, "Category").choices)) or (category in cats):
            qs = qs.filter(category=category)

    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 20)
    try:
        reports_page = paginator.page(page)
    except PageNotAnInteger:
        reports_page = paginator.page(1)
    except EmptyPage:
        reports_page = paginator.page(paginator.num_pages)

    allowed_choices = (
        list(getattr(Report, "Category").choices)
        if ("all" in cats)
        else [(c, d) for c, d in getattr(Report, "Category").choices if c in cats]
    )

    context = {
        "reports": reports_page,
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "teacher_name": teacher_name,
        "category": category if (("all" in cats) or (category in cats)) else "",
        "categories": allowed_choices,
    }
    return render(request, "reports/admin_reports.html", context)


@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["POST"])
def admin_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    report = get_object_or_404(Report, pk=pk)
    report.delete()
    messages.success(request, "تم حذف التقرير بنجاح.")
    return _safe_redirect(request, "reports:admin_reports")


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_print(request: HttpRequest, pk: int) -> HttpResponse:
    if request.user.is_staff:
        r = get_object_or_404(Report.objects.select_related("teacher"), pk=pk)
    else:
        r = get_object_or_404(Report.objects.select_related("teacher"), pk=pk, teacher=request.user)

    signer_label = "المعلّم"
    try:
        CAT_TO_ROLE = {
            "activity": "activity_officer",
            "volunteer": "volunteer_officer",
            "school_affairs": "affairs_officer",
            "admin": "admin_officer",
            "evidence": "teacher",
        }
        role_key = CAT_TO_ROLE.get(getattr(r, "category", None), "teacher")
        role_display_map = dict(ROLE_CHOICES) if "ROLE_CHOICES" in globals() else {}
        signer_label = role_display_map.get(role_key, "المعلّم")
    except Exception:
        pass

    return render(request, "reports/report_print.html", {"r": r, "signer_label": signer_label})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        from weasyprint import CSS, HTML
    except Exception:
        return HttpResponse("WeasyPrint غير مثبت. ثبّت الحزمة وشغّل مجددًا.", status=500)

    if request.user.is_staff:
        r = get_object_or_404(Report.objects.select_related("teacher"), pk=pk)
    else:
        r = get_object_or_404(Report.objects.select_related("teacher"), pk=pk, teacher=request.user)

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
    qs = Teacher.objects.all().order_by("-id")
    if term:
        qs = qs.filter(
            Q(name__icontains=term) | Q(phone__icontains=term) | Q(national_id__icontains=term)
        )
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
                    teacher = form.save(commit=False)
                    pwd = (form.cleaned_data.get("password") or "").strip()
                    if pwd:
                        teacher.set_password(pwd)
                    teacher.save()
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
                    updated = form.save(commit=False)
                    pwd = (form.cleaned_data.get("password") or "").strip()
                    if pwd:
                        updated.set_password(pwd)
                    else:
                        updated.password = teacher.password
                    updated.save()
                messages.success(request, "✏️ تم تحديث بيانات المستخدم بنجاح.")
                return redirect("reports:manage_teachers")
            except Exception:
                logger.exception("edit_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء التحديث.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherForm(instance=teacher)

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
        Ticket.objects.select_related("assignee")
        .prefetch_related(Prefetch("notes", queryset=notes_qs, to_attr="pub_notes"))
        .only("id", "title", "status", "department", "created_at", "assignee__name")
        .filter(creator=user)
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        base_qs = base_qs.filter(Q(title__icontains=q) | Q(id__icontains=q) | Q(assignee__name__icontains=q))

    counts = dict(base_qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    stats = {
        "open": counts.get("open", 0) + counts.get("new", 0),
        "in_progress": counts.get("in_progress", 0) + counts.get("pending", 0),
        "done": counts.get("done", 0),
        "rejected": counts.get("rejected", 0),
    }

    status = request.GET.get("status")
    qs = base_qs
    if status in {"open", "new", "in_progress", "pending", "done", "rejected"}:
        if status == "open":
            qs = qs.filter(Q(status="open") | Q(status="new"))
        elif status == "in_progress":
            qs = qs.filter(Q(status="in_progress") | Q(status="pending"))
        else:
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
        Ticket.objects.select_related("creator", "assignee").only(
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
                if is_owner and t.status != Ticket.Status.IN_PROGRESS:
                    old = t.status
                    t.status = Ticket.Status.IN_PROGRESS
                    try:
                        t.save(update_fields=["status"])
                    except Exception:
                        t.save()
                    changed = True
                    try:
                        TicketNote.objects.create(
                            ticket=t,
                            author=request.user,
                            body=f"إعادة فتح النقاش من قبل المُرسل. الحالة: {old} → in_progress",
                            is_public=True,
                        )
                    except Exception:
                        logger.exception("Failed to create system note on reopen")
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
            if is_owner and not can_act and not status_val:
                messages.success(request, "تمت إضافة الملاحظة وتحويل الحالة إلى قيد المعالجة.")
            else:
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


# ========= دعم الأقسام بالطريقتين =========

def _dept_code_for(dept_obj_or_code) -> str:
    """إرجاع slug (أو code fallback)."""
    if hasattr(dept_obj_or_code, "slug") and getattr(dept_obj_or_code, "slug"):
        return getattr(dept_obj_or_code, "slug")
    if hasattr(dept_obj_or_code, "code") and getattr(dept_obj_or_code, "code"):
        return getattr(dept_obj_or_code, "code")
    return str(dept_obj_or_code or "").strip()


def _arabic_label_for(dept_obj_or_code) -> str:
    """إرجاع اسم القسم بالعربية من كائن أو slug."""
    if hasattr(dept_obj_or_code, "name") and getattr(dept_obj_or_code, "name"):
        return dept_obj_or_code.name
    code = (
        getattr(dept_obj_or_code, "slug", None)
        or getattr(dept_obj_or_code, "code", None)   # احتياط
        or (dept_obj_or_code if isinstance(dept_obj_or_code, str) else "")
    )
    return ROLE_LABELS.get(code, code or "—")


def _resolve_department_by_code_or_pk(code_or_pk: str) -> Tuple[Optional[object], str, str]:
    """إيجاد القسم بالـ slug أو pk (إن توفر موديل Department)."""
    dept_obj = None
    dept_code = (code_or_pk or "").strip()

    if HAS_DEPT_MODEL and Department is not None:
        dept_obj = (
            Department.objects.filter(slug__iexact=dept_code).first()
            or Department.objects.filter(pk__iexact=dept_code).first()
        )
        if dept_obj:
            dept_code = _dept_code_for(dept_obj)

    dept_label = _arabic_label_for(dept_obj or dept_code)
    return dept_obj, dept_code, dept_label


def _members_for_department(dept_code: str):
    """
    إرجاع أعضاء القسم **بالدمج** بين:
    - العضويات عبر DepartmentMember
    - والأدوار عبر Teacher.role
    لضمان عدم فقدان بيانات قديمة.
    """
    qs_role = Teacher.objects.filter(role=dept_code, is_active=True)
    if HAS_DEPT_MODEL and DepartmentMember is not None and Department is not None:
        qs_m2m = Teacher.objects.filter(departmentmember__department__slug=dept_code, is_active=True)
        return (qs_role | qs_m2m).distinct().order_by("name")
    return qs_role.order_by("name")


def _user_department_codes(user) -> list[str]:
    """
    يعيد جميع أكواد الأقسام الخاصة بالمستخدم:
    - من role (إن كان != teacher)
    - ومن عضويات DepartmentMember (إن وجدت)
    """
    codes = set()
    role_code = getattr(user, "role", None)
    if role_code and role_code != "teacher":
        codes.add(role_code)
    if HAS_DEPT_MODEL and DepartmentMember is not None and Department is not None:
        try:
            # نفترض أن Department.slug هو المعيار
            mem_codes = (
                Department.objects.filter(departmentmember__teacher=user)
                .values_list("slug", flat=True)
            )
            for c in mem_codes:
                if c:
                    codes.add(c)
        except Exception:
            logger.exception("Failed to fetch user department codes")
    return list(codes)


def _tickets_stats_for_department(dept_code: str) -> dict:
    """إحصاءات التذاكر حسب القسم (department=slug)."""
    qs = Ticket.objects.filter(department=dept_code)
    return {
        "open": qs.filter(status__in={"new", "open"}).count(),
        "in_progress": qs.filter(status__in={"in_progress", "pending"}).count(),
        "done": qs.filter(status="done").count(),
    }


def _all_departments():
    """قائمة موحدة للأقسام (pk, code, name, is_active, members_count, stats)."""
    items = []
    if HAS_DEPT_MODEL and Department is not None:
        qs = Department.objects.all().order_by("id")
        if DepartmentMember is not None:
            qs = qs.annotate(members_count=Count("departmentmember"))
        for d in qs:
            code = _dept_code_for(d)
            stats = _tickets_stats_for_department(code)
            members_count = getattr(d, "members_count", None)
            if members_count is None:
                if hasattr(d, "members"):
                    members_count = d.members.count()
                elif DepartmentMember is not None:
                    members_count = DepartmentMember.objects.filter(department=d).count()
                else:
                    members_count = 0
            # أضف عدد الـ role legacy أيضاً (بدون تكرار)
            legacy_count = Teacher.objects.filter(role=code, is_active=True).exclude(
                id__in=Teacher.objects.filter(departmentmember__department=d).values_list("id", flat=True)
            ).count() if DepartmentMember is not None else Teacher.objects.filter(role=code, is_active=True).count()
            members_count = (members_count or 0) + (legacy_count or 0)

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
        for code, label in ROLE_LABELS.items():
            stats = _tickets_stats_for_department(code)
            members_count = _members_for_department(code).count()
            items.append(
                {
                    "pk": None,
                    "code": code,
                    "name": label,
                    "is_active": True,
                    "members_count": members_count,
                    "stats": stats,
                }
            )
    return items


# ---- نموذج قسم احتياطي + مزوّد موحّد ----
class _DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields: list[str] = []
        if model is not None:
            if hasattr(model, "name"):
                fields.append("name")
            if hasattr(model, "code"):
                fields.append("code")

    def clean(self):
        cleaned = super().clean()
        return cleaned


def get_department_form():
    """
    Wrapper موحّد لاستدعاء نموذج القسم.
    - إن وُجد DepartmentForm في forms.py نستخدمه.
    - وإلا نستخدم _DepartmentForm الاحتياطي.
    """
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
        "tickets_open": Ticket.objects.filter(
            status__in=["new", "open", "in_progress", "pending"]
        ).count(),
        "tickets_done": Ticket.objects.filter(status="done").count(),
        "tickets_rejected": Ticket.objects.filter(status__in=["rejected", "cancelled"]).count(),
        "has_dept_model": HAS_DEPT_MODEL,
    }

    # ===== دعم أنواع التقارير (ReportType) إن وُجدت =====
    has_reporttype = False
    reporttypes_count = 0
    try:
        # import محلي لتفادي الأعطال إن لم يكن الموديل موجودًا
        from .models import ReportType  # type: ignore

        has_reporttype = True
        # إن كان فيه is_active نعدّ الفعّالة فقط، وإلا نعدّ الكل
        if hasattr(ReportType, "is_active"):
            reporttypes_count = ReportType.objects.filter(is_active=True).count()
        else:
            reporttypes_count = ReportType.objects.count()
    except Exception:
        # يظل has_reporttype=False و reporttypes_count=0 بدون كسر الصفحة
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


# ---- الأقسام: حذف (code/slug أو pk) مع الحفاظ على توافق المسارات القديمة ----
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
    except Exception:
        logger.exception("department_delete failed")
        messages.error(request, "تعذّر حذف القسم.")
    return redirect("reports:departments_list")



from django.db.models import ManyToManyRel, ForeignObjectRel

def _dept_m2m_field_name_to_teacher(dep_obj) -> str | None:
    """
    يبحث عن حقل ManyToManyField (forward) في Department يشير إلى Teacher ويعيد اسمه إن وجد.
    يدعم حتى لو كان through=DepartmentMember باسم مخصص.
    """
    try:
        if dep_obj is None:
            return None
        for f in dep_obj._meta.get_fields():
            # نريد الـ forward M2M فقط
            if isinstance(f, ManyToManyField) and getattr(f.remote_field, "model", None) is Teacher:
                return f.name
    except Exception:
        logger.exception("Failed to detect forward M2M Department→Teacher")
    return None


def _deptmember_field_names() -> tuple[str | None, str | None]:
    """
    يعيد (dep_field_name, teacher_field_name) داخل DepartmentMember مهما كانت التسمية.
    يحاول اكتشافها من _meta، ثم يجرب أسماء شائعة كخطة بديلة.
    """
    dep_field = tea_field = None
    try:
        if DepartmentMember is None:
            return (None, None)

        for f in DepartmentMember._meta.get_fields():
            if isinstance(f, ForeignKey):
                if getattr(f.remote_field, "model", None) is Department and dep_field is None:
                    dep_field = f.name
                elif getattr(f.remote_field, "model", None) is Teacher and tea_field is None:
                    tea_field = f.name
            if dep_field and tea_field:
                break

        # خطة بديلة بأسماء شائعة
        if dep_field is None and any(hasattr(DepartmentMember, n) for n in ("department", "dept", "dept_fk")):
            for n in ("department", "dept", "dept_fk"):
                if hasattr(DepartmentMember, n):
                    dep_field = n
                    break
        if tea_field is None and any(hasattr(DepartmentMember, n) for n in ("teacher", "member", "user", "teacher_fk")):
            for n in ("teacher", "member", "user", "teacher_fk"):
                if hasattr(DepartmentMember, n):
                    tea_field = n
                    break
    except Exception:
        logger.exception("Failed to detect DepartmentMember FKs")

    return (dep_field, tea_field)


# ---- الأقسام: الأعضاء (تكليف/إلغاء) ----
def _dept_add_member(dep, teacher: Teacher) -> bool:
    """
    يحاول جميع المسارات الممكنة لإسناد معلّم إلى قسم.
    True عند النجاح، False إذا لم نتمكن عبر Department/DepartmentMember.
    """
    # 1) M2M مباشر على Department
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).add(teacher)
            return True
    except Exception:
        logger.exception("Add via Department M2M failed")

    # 2) موديل DepartmentMember (through أو مستقل)
    try:
        if DepartmentMember is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                DepartmentMember.objects.get_or_create(**kwargs)
                return True
    except Exception:
        logger.exception("Add via DepartmentMember failed")

    return False



def _dept_remove_member(dep, teacher: Teacher) -> bool:
    """
    يحاول إزالة الإسناد بجميع المسارات الممكنة.
    """
    # 1) M2M مباشر على Department
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).remove(teacher)
            return True
    except Exception:
        logger.exception("Remove via Department M2M failed")

    # 2) موديل DepartmentMember
    try:
        if DepartmentMember is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                deleted, _ = DepartmentMember.objects.filter(**kwargs).delete()
                return deleted > 0
    except Exception:
        logger.exception("Remove via DepartmentMember failed")

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
                            # ملاذ أخير: لا نكسر التجربة — نستخدم الدور التراثي
                            if getattr(teacher, "role", None) != dept_code:
                                teacher.role = dept_code
                                teacher.save(update_fields=["role"])
                                messages.warning(request, f"تم الإسناد عبر الدور (fallback). راجع بنية DepartmentMember لاحقًا.")
                            else:
                                messages.error(request, "تعذّر إسناد المعلّم — تحقق من بنية العلاقات.")
                    elif action == "remove":
                        ok = _dept_remove_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم إلغاء تكليف {teacher.name}.")
                        else:
                            # ملاذ أخير: إعادة الدور إلى 'teacher' إن كان هو نفس القسم
                            if getattr(teacher, "role", None) == dept_code:
                                teacher.role = "teacher"
                                teacher.save(update_fields=["role"])
                                messages.warning(request, f"تم الإلغاء عبر الدور (fallback).")
                            else:
                                messages.error(request, "تعذّر إلغاء التكليف — تحقق من بنية العلاقات.")
                    else:
                        messages.error(request, "إجراء غير معروف.")
            except Exception:
                logger.exception("department_members mutation failed")
                messages.error(request, "حدث خطأ أثناء حفظ التغييرات.")
        else:
            # بدون موديل Department: نستخدم الدور التراثي
            if action == "add":
                teacher.role = dept_code
                teacher.save(update_fields=["role"])
                messages.success(request, f"تم تعيين {teacher.name} لقسم «{dept_label}».")
            elif action == "remove":
                teacher.role = "teacher"
                teacher.save(update_fields=["role"])
                messages.success(request, f"تم إلغاء تعيين {teacher.name}.")
            else:
                messages.error(request, "إجراء غير معروف.")

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

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def reporttypes_list(request: HttpRequest) -> HttpResponse:
    if not (HAS_RTYPE and ReportType is not None):
        messages.error(request, "إدارة الأنواع تتطلب تفعيل موديل ReportType وتشغيل الهجرات.")
        # سنعرض قائمة مستخرجة من القيم الحالية في الحقل كمرجع فقط
        field = getattr(Report, "_meta", None).get_field("category") if hasattr(Report, "_meta") else None
        existing = list(getattr(field, "choices", [])) if field else []
        items = [{"code": v, "name": l, "is_active": True, "order": 0, "count": Report.objects.filter(category=v).count()} for v, l in existing]
        return render(request, "reports/reporttypes_list.html", {"items": items, "db_backed": False})

    qs = ReportType.objects.all().order_by("order", "name")
    items = []
    for rt in qs:
        cnt = Report.objects.filter(category=rt.code).count()
        items.append({"obj": rt, "code": rt.code, "name": rt.name, "is_active": rt.is_active, "order": rt.order, "count": cnt})
    return render(request, "reports/reporttypes_list.html", {"items": items, "db_backed": True})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def reporttype_create(request: HttpRequest) -> HttpResponse:
    if not (HAS_RTYPE and ReportType is not None):
        messages.error(request, "إنشاء الأنواع يتطلب تفعيل موديل ReportType.")
        return redirect("reports:reporttypes_list")

    FormCls = ReportTypeForm or (lambda *a, **k: forms.ModelForm)  # احتياطي
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
    FormCls = ReportTypeForm or (lambda *a, **k: forms.ModelForm)
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
    used = Report.objects.filter(category=obj.code).count()
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
    """
    يعيد قائمة أعضاء قسم معين بالاعتماد على الدمج بين DepartmentMember و Teacher.role.
    """
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
    qs = Ticket.objects.select_related("creator", "assignee").order_by("-created_at")

    if getattr(request.user, "role", None) != "manager":
        user_codes = _user_department_codes(request.user)
        qs = qs.filter(Q(assignee=request.user) | Q(department__in=user_codes))

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

    qs = Ticket.objects.select_related("creator", "assignee").filter(
        Q(assignee=user) | Q(assignee__isnull=True, department__in=user_codes)
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
