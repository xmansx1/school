# reports/permissions.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import wraps
from typing import Iterable, Set, Any

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.db.models import QuerySet


__all__ = [
    "role_required",
    "allowed_categories_for",
    "restrict_queryset_for_user",
]


# ==============================
# أدوات داخلية
# ==============================
def _user_role(user):
    """
    يعيد كائن Role المرتبط بالمستخدم إن وجد، وإلا None.
    نتجنّب الاستيراد العلوي لتفادي الدوّارات بين modules.
    """
    try:
        return getattr(user, "role", None)
    except Exception:
        return None


def _user_role_slug(user) -> str | None:
    """
    يعيد slug للدور الحالي للمستخدم (FK إلى Role) أو None إن لم يوجد.
    """
    try:
        role = _user_role(user)
        return getattr(role, "slug", None) if role else None
    except Exception:
        return None


# ==============================
# ديكوريتر حصر الوصول حسب الدور (بالـ slug)
# ==============================
def role_required(allowed_roles: Iterable[str]):
    """
    مثال الاستعمال:
        @login_required(login_url="reports:login")
        @role_required({"manager"})
        def some_view(...): ...
    - السوبر يمر دائمًا.
    - المقارنة بالـ slug للأدوار.
    """
    allowed = set(allowed_roles or [])

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user
            if not getattr(user, "is_authenticated", False):
                return redirect("reports:login")

            if getattr(user, "is_superuser", False):
                return view_func(request, *args, **kwargs)

            role_slug = _user_role_slug(user)
            if role_slug in allowed:
                return view_func(request, *args, **kwargs)

            messages.error(request, "لا تملك صلاحية الوصول إلى هذه الصفحة.")
            return redirect("reports:home")

        return _wrapped

    return decorator


# ==============================
# صلاحيات عرض التصنيفات ديناميكيًا من قاعدة البيانات
# ==============================
def allowed_categories_for(user) -> Set[str]:
    """
    يعيد مجموعة أكواد ReportType المسموحة للمستخدم في لوحة التقارير.
    - يرجع {"all"} إن كان السوبر أو إذا كان دور المستخدم يملك can_view_all_reports=True.
    - خلاف ذلك، يرجع مجموعة الأكواد المرتبطة عبر M2M: Role.allowed_reporttypes.
    - في حال عدم وجود دور/أخطاء: يرجع set() آمنة.
    """
    try:
        if getattr(user, "is_superuser", False):
            return {"all"}

        role = _user_role(user)
        if not role:
            return set()

        # import محلي لتجنّب الدوّارات
        # Role.allowed_reporttypes → ReportType(code)
        if getattr(role, "can_view_all_reports", False):
            return {"all"}

        try:
            # نجلب الأكواد مباشرة من الـ M2M
            codes = set(role.allowed_reporttypes.values_list("code", flat=True))
            return {c for c in codes if c}  # تنظيف أي فراغات/None احتياطًا
        except Exception:
            return set()
    except Exception:
        # أي خطأ غير متوقع → إرجاع مجموعة فارغة كخيار آمن
        return set()


# ==============================
# تقييد QuerySet بحسب المستخدم
# ==============================
def restrict_queryset_for_user(qs: QuerySet[Any], user) -> QuerySet[Any]:
    """
    يقيّد QuerySet للتقارير بحسب دور المستخدم:
    - السوبر/المدير (slug="manager"): بدون قيود.
    - المعلّم (slug="teacher"): يرى تقاريره فقط.
    - بقية الأدوار: تقارير ضمن التصنيفات المسموح بها من DB (M2M).
    ملاحظات:
      * نفترض أن qs يعود لـ Report أو QuerySet فيه الحقل category (FK→ReportType) و teacher.
      * لا حاجة لاستيراد Report هنا؛ نعمل على qs المُمرَّر كما هو.
    """
    # سوبر أو مدير: الكل
    role_slug = _user_role_slug(user)
    if getattr(user, "is_superuser", False) or role_slug == "manager":
        return qs

    # معلّم: تقاريره فقط
    if role_slug == "teacher":
        return qs.filter(teacher=user)

    # أدوار أخرى: بحسب الأنواع المسموحة
    allowed = allowed_categories_for(user)
    if not allowed:
        return qs.none()
    if "all" in allowed:
        return qs

    # التصنيف FK إلى ReportType؛ نفلتر على code
    return qs.filter(category__code__in=allowed)
