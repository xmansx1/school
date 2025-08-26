from __future__ import annotations
# أعلى الملف (إن لم تكن موجودة)
import os, traceback

import logging
from datetime import date
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods
from django.template.loader import render_to_string
from django.http import HttpResponse

from .forms import ActivityReportForm, TeacherForm
from .models import ActivityReport, Teacher

logger = logging.getLogger(__name__)

# ---------- أدوات مساعدة ----------

def _safe_next_url(next_url: str | None) -> str | None:
    """
    يمنع إعادة التوجيه لخارج الموقع (open redirect).
    يسمح فقط بالمسارات الداخلية مثل: /home/ أو /my-reports/
    """
    if not next_url:
        return None
    parsed = urlparse(next_url)
    return next_url if (parsed.scheme == "" and parsed.netloc == "") else None


def _parse_date_safe(value: str | None) -> date | None:
    """
    يحوّل نص التاريخ إلى تاريخ. يعيد None لو كان الإدخال غير صالح.
    """
    if not value:
        return None
    return parse_date(value)


def _is_staff(user) -> bool:
    return bool(user and user.is_authenticated and user.is_staff)


# ---------- الدخول/الخروج ----------

@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """
    تسجيل الدخول باستخدام (رقم الهوية + كلمة المرور).
    يدعم ?next=/path لإعادة التوجيه الآمن بعد الدخول.
    """
    if request.user.is_authenticated:
        return redirect("reports:home")

    if request.method == "POST":
        national_id = (request.POST.get("national_id") or "").strip()
        password = request.POST.get("password") or ""
        # تمرير username لضمان التوافق مع أي Backend يعتمد USERNAME_FIELD
        user = authenticate(request, username=national_id, password=password)

        if user is not None:
            login(request, user)
            next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
            return redirect(next_url or "reports:home")
        messages.error(request, "رقم الهوية أو كلمة المرور غير صحيحة")

    context = {"next": _safe_next_url(request.GET.get("next"))}
    return render(request, "reports/login.html", context)


@require_http_methods(["POST", "GET"])
def logout_view(request: HttpRequest) -> HttpResponse:
    """
    تسجيل الخروج. يقبل GET لتبسيط الاستخدام من زر/رابط.
    """
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "تم تسجيل الخروج بنجاح.")
    return redirect("reports:login")


# ---------- الواجهة الرئيسية للمعلم ----------

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def home(request: HttpRequest) -> HttpResponse:
    """
    لوحة المعلم: إحصائيات + آخر 5 تقارير للمعلم الحالي.
    - تتعامل بأمان مع قواعد بيانات فارغة/أخطاء.
    - تسجّل الاستثناء في اللوجز.
    - يمكن إظهار الـ traceback مؤقتًا عند تفعيل SHOW_ERRORS=1.
    """
    stats = {"today_count": 0, "total_count": 0, "last_program": "—"}
    recent_reports = []

    try:
        # استعلامات مقيّدة بالمستخدم الحالي
        my_qs = (
            ActivityReport.objects
            .select_related("teacher")
            .filter(teacher=request.user)
        )

        today = timezone.localdate()

        stats["total_count"] = my_qs.count()
        stats["today_count"] = my_qs.filter(report_date=today).count()

        last_report = my_qs.order_by("-report_date", "-id").first()
        stats["last_program"] = (last_report.program_name if last_report else "—")

        recent_reports = my_qs.order_by("-report_date", "-id")[:5]

        # ✅ انتبه لمسار القالب الصحيح
        return render(
            request,
            "reports/home.html",
            {"stats": stats, "recent_reports": recent_reports},
        )

    except Exception as exc:
        # يُسجَّل كامل الخطأ في لوجز Render
        logger.exception("Home view failed")
        # إظهار الخطأ على الصفحة مؤقتًا عند الحاجة للتشخيص
        if settings.DEBUG or os.getenv("SHOW_ERRORS") == "1":
            return HttpResponse(
                "<h2>Home exception</h2><pre>"
                + traceback.format_exc()
                + "</pre>",
                status=500,
            )
        # سلوك الإنتاج الطبيعي: 500 قياسي
        raise


# ---------- إضافة وعرض تقارير المعلم ----------

@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def add_report(request: HttpRequest) -> HttpResponse:
    """
    إضافة تقرير جديد. يربط التقرير تلقائيًا بالمعلم الحالي.
    """
    if request.method == "POST":
        form = ActivityReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save(commit=False)
            report.teacher = request.user  # ربط التقرير بالمعلم الحالي
            report.save()
            messages.success(request, "تم إضافة التقرير بنجاح ✅")
            return redirect("reports:my_reports")
        else:
            messages.error(request, "فضلاً تحقق من الحقول وأعد المحاولة.")
    else:
        form = ActivityReportForm()

    return render(request, "reports/add_report.html", {"form": form})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_reports(request: HttpRequest) -> HttpResponse:
    """
    عرض جميع تقارير المعلم مع فلترة اختيارية بالتاريخ (من/إلى) + ترقيم صفحات.
    """
    qs = (
        ActivityReport.objects
        .select_related("teacher")
        .filter(teacher=request.user)
        .order_by("-report_date", "-id")
    )

    # فلترة بالتاريخ
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)

    # ترقيم الصفحات
    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 10)  # 10 تقارير في الصفحة
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
    }
    return render(request, "reports/my_reports.html", context)


# ---------- لوحة المدير (عرض/حذف التقارير) ----------

@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET"])
def admin_reports(request: HttpRequest) -> HttpResponse:
    """
    قائمة تقارير لجميع المعلمين للمدير فقط (is_staff=True) مع فلاتر اختيارية.
    يدعم فلترة بالتاريخ وبحسب المعلّم (teacher_national_id).
    """
    qs = ActivityReport.objects.select_related("teacher").order_by("-report_date", "-id")

    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    teacher_nid = (request.GET.get("teacher_national_id") or "").strip()

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if teacher_nid:
        qs = qs.filter(teacher__national_id=teacher_nid)

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
        "teacher_national_id": teacher_nid,
    }
    return render(request, "reports/admin_reports.html", context)


@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["POST"])
def admin_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    """
    حذف تقرير واحد بواسطة المدير فقط (POST + CSRF).
    """
    report = get_object_or_404(ActivityReport, pk=pk)
    report.delete()
    messages.success(request, "تم حذف التقرير بنجاح.")
    next_url = _safe_next_url(request.POST.get("next"))
    return redirect(next_url or "reports:admin_reports")


# ---------- طباعة/تصدير التقارير ----------

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_print(request: HttpRequest, pk: int) -> HttpResponse:
    """
    عرض HTML قابل للطباعة لتقرير واحد.
    المعلم يرى تقاريره فقط. المدير (is_staff) يرى الكل.
    """
    if request.user.is_staff:
        report = get_object_or_404(ActivityReport.objects.select_related("teacher"), pk=pk)
    else:
        report = get_object_or_404(ActivityReport.objects.select_related("teacher"), pk=pk, teacher=request.user)
    return render(request, "reports/report_print.html", {"r": report})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    """
    يُصدر PDF من نفس قالب الطباعة باستخدام WeasyPrint.
    يتطلب: pip install weasyprint ومكتبات النظام.
    """
    try:
        from weasyprint import HTML, CSS
    except Exception:
        return HttpResponse("WeasyPrint غير مثبت. ثبّت الحزمة وشغّل مجدداً.", status=500)

    if request.user.is_staff:
        report = get_object_or_404(ActivityReport.objects.select_related("teacher"), pk=pk)
    else:
        report = get_object_or_404(ActivityReport.objects.select_related("teacher"), pk=pk, teacher=request.user)

    html = render_to_string("reports/report_print.html", {"r": report, "for_pdf": True}, request=request)
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf(
        stylesheets=[CSS(string="""@page { size: A4; margin: 14mm 12mm; }""")]
    )
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="report-{report.pk}.pdf"'
    return resp


# ---------- إدارة المعلّمين ----------

@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET"])
def manage_teachers(request: HttpRequest) -> HttpResponse:
    teachers = Teacher.objects.all().order_by("-id")
    return render(request, "reports/manage_teachers.html", {"teachers": teachers})


@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def add_teacher(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = TeacherForm(request.POST)
        if form.is_valid():
            teacher = form.save(commit=False)
            # كلمة المرور
            password = form.cleaned_data.get("password")
            if password:
                teacher.set_password(password)
            # نوع الحساب (معلم / مدير)
            teacher.is_staff = (request.POST.get("is_staff") == "1")
            teacher.save()
            messages.success(request, "✅ تم إضافة المعلم بنجاح")
            return redirect("reports:manage_teachers")
    else:
        form = TeacherForm()
    return render(request, "reports/add_teacher.html", {"form": form})


@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def edit_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    teacher = get_object_or_404(Teacher, pk=pk)
    if request.method == "POST":
        form = TeacherForm(request.POST, instance=teacher)
        if form.is_valid():
            teacher = form.save(commit=False)
            password = form.cleaned_data.get("password")
            if password:
                teacher.set_password(password)
            teacher.is_staff = (request.POST.get("is_staff") == "1")
            teacher.save()
            messages.success(request, "✏️ تم تحديث بيانات المعلم بنجاح")
            return redirect("reports:manage_teachers")
    else:
        form = TeacherForm(instance=teacher)
    return render(request, "reports/edit_teacher.html", {"form": form, "teacher": teacher})


@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["POST"])
def delete_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    teacher = get_object_or_404(Teacher, pk=pk)
    teacher.delete()
    messages.success(request, "🗑️ تم حذف المعلم")
    return redirect("reports:manage_teachers")
