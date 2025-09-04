# reports/permissions.py
from __future__ import annotations

from functools import wraps
from typing import Iterable, Set

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.db.models import QuerySet

from .models import Report, Teacher

__all__ = [
    "STAFF_ROLES",
    "ROLE_TO_CATEGORIES",
    "role_required",
    "allowed_categories_for",
    "restrict_queryset_for_user",
]

# ==============================
# أدوات داخلية لمواءمة الأكواد
# ==============================
def _all_category_codes() -> Set[str]:
    """
    يعيد جميع أكواد التصنيفات المعرفة في Report.Category.
    لو حدث خطأ (نادرًا) يعيد مجموعة فارغة.
    """
    try:
        return {code for code, _ in Report.Category.choices}
    except Exception:
        return set()


def _cat(code_name: str, default_value: str) -> str:
    """
    يحاول جلب كود التصنيف من Report.Category.<CONST> وإلا يعيد قيمة افتراضية
    لضمان عمل النظام حتى لو تغيّر اسم الثابت في الموديل.
    """
    return getattr(Report.Category, code_name, default_value)


# ضبط الأكواد الافتراضية المتداولة في المشروع
CAT_ACTIVITY       = _cat("ACTIVITY", "activity")
CAT_VOLUNTEER      = _cat("VOLUNTEER", "volunteer")
CAT_SCHOOL_AFFAIRS = _cat("SCHOOL_AFFAIRS", "school_affairs")
CAT_ADMIN          = _cat("ADMIN", "admin")
CAT_EVIDENCE       = _cat("EVIDENCE", "evidence")  # مستخدم في الطباعة كـ "teacher" افتراضيًا

# ==============================
# تعريفات أدوار وصلاحيات
# ==============================
# الأدوار التي تُعامل كموظفين داخل النظام (ليست بديلًا عن is_staff)
STAFF_ROLES: Set[str] = {
    "manager",
    "activity_officer",
    "volunteer_officer",
    "affairs_officer",
    "admin_officer",
}

# خرائط الدور ← التصنيفات المسموحة له داخل لوحة التقارير الإدارية
ROLE_TO_CATEGORIES: dict[str, Set[str]] = {
    # المدير يرى كل شيء (سنحسب الكل ديناميكيًا عند الطلب)
    "manager": _all_category_codes(),
    # الضباط: تصنيف محدد
    "activity_officer": {CAT_ACTIVITY},
    "volunteer_officer": {CAT_VOLUNTEER},
    "affairs_officer": {CAT_SCHOOL_AFFAIRS},
    "admin_officer": {CAT_ADMIN},
    # المعلّم لا يملك صلاحية لوحة المدير
    "teacher": set(),
}


# ==============================
# ديكوريتور حصر الوصول حسب الدور
# ==============================
def role_required(allowed_roles: Iterable[str]):
    """
    يستعمل مع views الإدارية. مثال:
        @login_required(login_url="reports:login")
        @role_required({"manager"})
    السوبر يمر دائمًا. المستخدمون بدون الأدوار المطلوبة تُعرض لهم رسالة ويرجَعون للـ home.
    """
    allowed = set(allowed_roles or [])

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user
            if not getattr(user, "is_authenticated", False):
                # غالبًا يوجد login_required قبل هذا الديكوريتور؛ نعيد التوجيه احتياطيًا
                return redirect("reports:login")

            if getattr(user, "is_superuser", False):
                return view_func(request, *args, **kwargs)

            role = getattr(user, "role", None)
            if role in allowed:
                return view_func(request, *args, **kwargs)

            messages.error(request, "لا تملك صلاحية الوصول إلى هذه الصفحة.")
            return redirect("reports:home")

        return _wrapped

    return decorator


# ==============================
# صلاحيات عرض التقارير الإدارية
# ==============================
def allowed_categories_for(user: Teacher) -> Set[str]:
    """
    تعيد مجموعة التصنيفات المسموح عرضها للمستخدم داخل لوحة الإدارة.
    - السوبر/المدير: جميع التصنيفات.
    - الضباط: تصنيفهم فقط.
    - المعلم: مجموعة فارغة.
    ملاحظة: لا نُرجع {"all"} لنبقى منسجمين مع سياسات الفلترة في views؛
    إرجاع مجموعة "كل الأكواد" يعادل "all" عمليًا في الفلترة.
    """
    if getattr(user, "is_superuser", False):
        return _all_category_codes()

    role = getattr(user, "role", None)
    if role == "manager":
        return _all_category_codes()

    return ROLE_TO_CATEGORIES.get(role, set())


def restrict_queryset_for_user(qs: QuerySet, user: Teacher) -> QuerySet:
    """
    يقيّد QuerySet للتقارير بحسب دور المستخدم:
    - السوبر/المدير: بدون قيود.
    - المعلّم: يرى تقاريره فقط.
    - الضبّاط: تقارير تصنيفهم فقط.
    """
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) == "manager":
        return qs

    role = getattr(user, "role", None)
    if role == "teacher":
        return qs.filter(teacher=user)

    allowed = ROLE_TO_CATEGORIES.get(role, set())
    if not allowed:
        # إن لم يكن له تصنيفات مخصصة، لا يرى شيئًا
        return qs.none()

    return qs.filter(category__in=allowed)
