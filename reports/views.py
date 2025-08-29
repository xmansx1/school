# reports/views.py
import os
import traceback
import logging
from datetime import date
from urllib.parse import urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import transaction, IntegrityError
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .forms import ReportForm, TeacherForm
from .models import Report, Teacher
from .permissions import allowed_categories_for, restrict_queryset_for_user, role_required

logger = logging.getLogger(__name__)


# ---------- أدوات مساعدة عامة ----------
def _safe_next_url(next_url: str | None) -> str | None:
    """
    يمنع إعادة التوجيه لخارج الموقع (open redirect).
    يسمح فقط بالمسارات الداخلية مثل: /home/ أو /my-reports/
    """
    if not next_url:
        return None
    parsed = urlparse(next_url)
    return next_url if (parsed.scheme == "" and parsed.netloc == "") else None


def _safe_redirect(request: HttpRequest, fallback_name: str) -> HttpResponse:
    """
    إعادة توجيه آمنة إلى ?next=... إذا كانت داخل نفس الموقع، وإلا إلى fallback.
    """
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}):
        return redirect(nxt)
    return redirect(fallback_name)


def _parse_date_safe(value: str | None) -> date | None:
    """
    يحوّل نص التاريخ إلى date. يعيد None لو كان الإدخال غير صالح.
    """
    if not value:
        return None
    return parse_date(value)


def _is_staff(user) -> bool:
    """
    يسمح بدخول لوحة الإدارة لأي مستخدم staff (مدير/مسؤولي الأقسام).
    Teacher.is_staff يُضبط تلقائيًا من الدور داخل الموديل.
    """
    return bool(user and user.is_authenticated and user.is_staff)


# ---------- الدخول/الخروج ----------
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    """
    تسجيل الدخول باستخدام (رقم الجوال + كلمة المرور).
    يدعم ?next=/path لإعادة التوجيه الآمن بعد الدخول.
    """
    if request.user.is_authenticated:
        return redirect("reports:home")

    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=phone, password=password)  # USERNAME_FIELD=phone

        if user is not None:
            login(request, user)
            next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
            return redirect(next_url or "reports:home")
        messages.error(request, "رقم الجوال أو كلمة المرور غير صحيحة")

    context = {"next": _safe_next_url(request.GET.get("next"))}
    return render(request, "reports/login.html", context)


@require_http_methods(["GET", "POST"])
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
    """
    stats = {"today_count": 0, "total_count": 0, "last_title": "—"}
    recent_reports = []

    try:
        my_qs = (
            Report.objects
            .select_related("teacher")
            .filter(teacher=request.user)
        )

        today = timezone.localdate()
        stats["total_count"] = my_qs.count()
        stats["today_count"] = my_qs.filter(report_date=today).count()

        last_report = my_qs.order_by("-report_date", "-id").first()
        stats["last_title"] = (last_report.title if last_report else "—")

        recent_reports = my_qs.order_by("-report_date", "-id")[:5]

        return render(request, "reports/home.html", {"stats": stats, "recent_reports": recent_reports})

    except Exception:
        logger.exception("Home view failed")
        if settings.DEBUG or os.getenv("SHOW_ERRORS") == "1":
            return HttpResponse(
                "<h2>Home exception</h2><pre>" + traceback.format_exc() + "</pre>",
                status=500,
            )
        raise


# ---------- إضافة وعرض تقارير المعلم ----------
@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def add_report(request: HttpRequest) -> HttpResponse:
    """
    إضافة تقرير جديد. يربط التقرير تلقائيًا بالمعلم الحالي.
    """
    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES)
        if form.is_valid():
            report = form.save(commit=False)
            report.teacher = request.user
            report.save()
            messages.success(request, "تم إضافة التقرير بنجاح ✅")
            return redirect("reports:my_reports")
        else:
            messages.error(request, "فضلاً تحقق من الحقول وأعد المحاولة.")
    else:
        form = ReportForm()

    return render(request, "reports/add_report.html", {"form": form})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_reports(request: HttpRequest) -> HttpResponse:
    """
    عرض جميع تقارير المعلم مع فلترة اختيارية بالتاريخ (من/إلى) + ترقيم صفحات.
    """
    qs = (
        Report.objects
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


# ---------- لوحة التقارير الإدارية ----------
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET"])
def admin_reports(request: HttpRequest) -> HttpResponse:
    """
    قائمة تقارير لجميع المعلّمين لمستخدمي staff فقط.
    تُقيَّد رؤية التقارير حسب الدور عبر categories_for_user.
    """
    cats = allowed_categories_for(request.user)  # قد تكون {"all"} أو {"activity"}... إلخ

    qs = Report.objects.select_related("teacher").order_by("-report_date", "-id")

    # تقييد حسب التصنيفات المسموحة
    if "all" not in cats:
        # نتجاهل owner_only هنا لأنه لا يصل للوحة أصلاً، لكن من باب الأمان:
        allowed = [c for c, _ in getattr(Report, "Category").choices if c in cats]
        qs = qs.filter(category__in=allowed)

    # فلاتر اختيارية
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    teacher_nid = (request.GET.get("teacher_national_id") or "").strip()
    category = (request.GET.get("category") or "").strip()

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if teacher_nid:
        qs = qs.filter(teacher__national_id=teacher_nid)

    if category:
        # لا نسمح بفئة غير مسموحة
        if "all" in cats:
            qs = qs.filter(category=category)
        else:
            if category in cats:
                qs = qs.filter(category=category)

    # ترقيم الصفحات
    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 20)
    try:
        reports_page = paginator.page(page)
    except PageNotAnInteger:
        reports_page = paginator.page(1)
    except EmptyPage:
        reports_page = paginator.page(paginator.num_pages)

    # اعرض فقط التصنيفات المسموحة في عناصر الاختيار
    if "all" in cats:
        allowed_choices = list(getattr(Report, "Category").choices)
    else:
        allowed_choices = [(c, d) for c, d in getattr(Report, "Category").choices if c in cats]

    context = {
        "reports": reports_page,
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "teacher_national_id": teacher_nid,
        "category": category if (("all" in cats) or (category in cats)) else "",
        "categories": allowed_choices,
    }
    return render(request, "reports/admin_reports.html", context)


@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["POST"])
def admin_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    """
    حذف تقرير واحد بواسطة مستخدم staff (POST + CSRF).
    """
    report = get_object_or_404(Report, pk=pk)
    report.delete()
    messages.success(request, "تم حذف التقرير بنجاح.")
    return _safe_redirect(request, "reports:admin_reports")


# ---------- طباعة/تصدير التقارير ----------
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_print(request: HttpRequest, pk: int) -> HttpResponse:
    """
    عرض HTML قابل للطباعة لتقرير واحد.
    المعلم يرى تقاريره فقط. staff يرى الجميع.
    """
    if request.user.is_staff:
        report = get_object_or_404(Report.objects.select_related("teacher"), pk=pk)
    else:
        report = get_object_or_404(Report.objects.select_related("teacher"), pk=pk, teacher=request.user)
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
        return HttpResponse("WeasyPrint غير مثبت. ثبّت الحزمة وشغّل مجددًا.", status=500)

    if request.user.is_staff:
        report = get_object_or_404(Report.objects.select_related("teacher"), pk=pk)
    else:
        report = get_object_or_404(Report.objects.select_related("teacher"), pk=pk, teacher=request.user)

    html = render_to_string("reports/report_print.html", {"r": report, "for_pdf": True}, request=request)
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf(
        stylesheets=[CSS(string="""@page { size: A4; margin: 14mm 12mm; }""")]
    )
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="report-{report.pk}.pdf"'
    return resp

# ---------- إدارة المعلّمين ----------
from django.db import transaction, IntegrityError
from django.core.paginator import Paginator
from django.db.models import Q

@login_required(login_url="reports:login")
@role_required({"manager"})  # المدير فقط
@require_http_methods(["GET"])
def manage_teachers(request: HttpRequest) -> HttpResponse:
    """
    عرض قائمة المعلّمين مع بحث وتقسيم صفحات.
    البحث يشمل: الاسم / الجوال / رقم الهوية.
    """
    term = (request.GET.get("q") or "").strip()
    qs = Teacher.objects.all().order_by("-id")
    if term:
        qs = qs.filter(
            Q(name__icontains=term) |
            Q(phone__icontains=term) |
            Q(national_id__icontains=term)
        )

    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    ctx = {"teachers_page": page, "term": term}
    return render(request, "reports/manage_teachers.html", ctx)


@login_required(login_url="reports:login")
@role_required({"manager"})  # المدير فقط
@require_http_methods(["GET", "POST"])
def add_teacher(request: HttpRequest) -> HttpResponse:
    """
    إضافة مستخدم جديد:
    - الدور يُحدّد من الحقل role في الفورم.
    - كلمة المرور تُحفظ فقط إن أُدخلت (وإلا تُترك فارغة).
    - any is_staff logic handled by the model/form.
    """
    if request.method == "POST":
        form = TeacherForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    teacher = form.save(commit=False)

                    # كلمة المرور (إن وُجدت)
                    password = (form.cleaned_data.get("password") or "").strip()
                    if password:
                        teacher.set_password(password)

                    teacher.save()

                messages.success(request, "✅ تم إضافة المستخدم بنجاح.")
                # إعادة توجيه آمنة
                next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                return redirect(next_url or "reports:manage_teachers")

            except IntegrityError:
                messages.error(request, "تعذّر الحفظ: قد يكون رقم الجوال أو الهوية مستخدمًا مسبقًا.")
            except Exception:
                messages.error(request, "حدث خطأ غير متوقع أثناء الحفظ. جرّب لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherForm()

    return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})


@login_required(login_url="reports:login")
@role_required({"manager"})  # المدير فقط
@require_http_methods(["GET", "POST"])
def edit_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    """
    تعديل بيانات مستخدم:
    - لا نغيّر كلمة المرور إن تُركت فارغة.
    - تغيير الدور يُحفظ عبر الفورم، وأي ضبط للصلاحيات يتم في الموديل/الفورم.
    """
    teacher = get_object_or_404(Teacher, pk=pk)

    if request.method == "POST":
        form = TeacherForm(request.POST, instance=teacher)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated = form.save(commit=False)

                    # لو الحقل فارغ نحافظ على الهاش القديم
                    if not (form.cleaned_data.get("password") or "").strip():
                        updated.password = teacher.password
                    else:
                        # لو أُدخلت كلمة مرور جديدة، الفورم قد يكون جهّزها،
                        # لكن نضمن التحديث لو احتجنا:
                        pwd = form.cleaned_data.get("password")
                        if pwd:
                            updated.set_password(pwd)

                    updated.save()

                messages.success(request, "✏️ تم تحديث بيانات المستخدم بنجاح.")
                return redirect("reports:manage_teachers")

            except Exception:
                messages.error(request, "حدث خطأ غير متوقع أثناء التحديث.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherForm(instance=teacher)

    return render(
        request,
        "reports/edit_teacher.html",
        {"form": form, "teacher": teacher, "title": "تعديل مستخدم"},
    )


@login_required(login_url="reports:login")
@role_required({"manager"})  # المدير فقط
@require_http_methods(["POST"])
def delete_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    """
    حذف مستخدم (POST فقط + يتطلب CSRF في القالب).
    """
    teacher = get_object_or_404(Teacher, pk=pk)
    try:
        with transaction.atomic():
            teacher.delete()
        messages.success(request, "🗑️ تم حذف المستخدم.")
    except Exception:
        messages.error(request, "تعذّر حذف المستخدم. حاول لاحقًا.")

    # إعادة توجيه آمنة بعد الحذف
    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:manage_teachers")
