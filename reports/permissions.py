# reports/permissions.py
from __future__ import annotations

from functools import wraps
from typing import Iterable, Set

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import Report, Teacher


# ==============================
# أدوات مساعدة عامة
# ==============================
def _all_category_codes() -> Set[str]:
    """جميع أكواد التصنيفات المعرفة في Report.Category."""
    try:
        return {code for code, _ in Report.Category.choices}
    except Exception:
        return set()


# أدوار الإدارة التي تُعامل كموظفين داخل النظام (اختياري للاستخدام الخارجي)
STAFF_ROLES: Set[str] = {
    "manager",
    "activity_officer",
    "volunteer_officer",
    "affairs_officer",
    "admin_officer",
}

# خرائط الدور ← التصنيفات المسموحة في لوحة المدير
ROLE_TO_CATEGORIES: dict[str, Set[str]] = {
    # المدير يرى كل شيء
    "manager": _all_category_codes(),
    # الضباط يرون تصنيفهم فقط
    "activity_officer": {getattr(Report.Category, "ACTIVITY", "activity")},
    "volunteer_officer": {getattr(Report.Category, "VOLUNTEER", "volunteer")},
    "affairs_officer": {getattr(Report.Category, "SCHOOL_AFFAIRS", "school_affairs")},
    "admin_officer": {getattr(Report.Category, "ADMIN", "admin")},
    # المعلّم لا يملك صلاحية رؤية لوحة المدير (سيتم منعه قبلها)،
    # وإن استُخدمت هذه الدالة له فسترجع مجموعة فارغة.
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
    - يسمح للسوبر تلقائيًا.
    - يكتب رسالة خطأ ويعيد التوجيه للصفحة الرئيسية عند عدم السماح.
    """
    allowed = set(allowed_roles or [])

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user
            if not getattr(user, "is_authenticated", False):
                # عادة يوجد login_required قبل هذا الديكوريتور؛ نعيد التوجيه احتياطيًا
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
# صلاحيات عرض التقارير في لوحة المدير
# ==============================
def allowed_categories_for(user: Teacher) -> Set[str]:
    """
    تعيد مجموعة التصنيفات المسموح عرضها للمستخدم داخل لوحة المدير.
    - المدير/السوبر: كل التصنيفات.
    - الضباط: تصنيفهم فقط.
    - المعلم: مجموعة فارغة (لا يملك صلاحية لوحة المدير).
    """
    if getattr(user, "is_superuser", False):
        return _all_category_codes()

    role = getattr(user, "role", None)
    if role == "manager":
        return _all_category_codes()

    return ROLE_TO_CATEGORIES.get(role, set())


def restrict_queryset_for_user(qs, user: Teacher):
    """
    يقيّد QuerySet للتقارير بحسب دور المستخدم:
    - المدير/السوبر: بدون قيود.
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
        # إن لم يكن له تصنيفات مخصصة، لا يرى شيء
        return qs.none()

    return qs.filter(category__in=allowed)
