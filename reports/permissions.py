# reports/permissions.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import wraps
from typing import Iterable, Set, Any, Optional

from django.contrib import messages
from django.db.models import QuerySet, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import Department  # نحتاجه لاكتشاف قسم المسؤول


__all__ = [
    "get_officer_department",
    "is_officer",
    "role_required",
    "allowed_categories_for",
    "restrict_queryset_for_user",
]


# ==============================
# أدوات داخلية
# ==============================
def _user_role(user):
    """يعيد كائن Role المرتبط بالمستخدم إن وجد، وإلا None."""
    try:
        return getattr(user, "role", None)
    except Exception:
        return None


def _user_role_slug(user) -> Optional[str]:
    """يعيد slug للدور الحالي للمستخدم أو None إن لم يوجد."""
    role = _user_role(user)
    return getattr(role, "slug", None) if role else None


# ==============================
# اكتشاف “مسؤول قسم”
# ==============================
def get_officer_department(user) -> Optional[Department]:
    """
    يبحث عن القسم المرتبط بدور المستخدم:
      1) يطابق Department.slug == user.role.slug
      2) إن فشل، يطابق Department.role_label (case-insensitive) مع user.role.name
    يعيد None إذا لم يُعثر على قسم نشط.
    """
    if not getattr(user, "is_authenticated", False):
        return None

    role = _user_role(user)
    if not role:
        return None

    qs = Department.objects.filter(is_active=True).only("id", "name", "slug")

    dept = None
    if getattr(role, "slug", None):
        dept = qs.filter(slug=role.slug).first()

    if not dept and getattr(role, "name", None):
        dept = qs.filter(role_label__iexact=role.name).first()

    return dept


def is_officer(user) -> bool:
    """هل المستخدم مسؤول قسم؟"""
    return bool(get_officer_department(user))


# ==============================
# ديكوريتر حصر الوصول حسب الدور (بالـ slug)
# ==============================
def role_required(allowed_roles: Iterable[str]):
    """
    مثال:
        @login_required(login_url="reports:login")
        @role_required({"manager"})
        def some_view(...): ...
    - السوبر يمر دائمًا.
    - المقارنة تتم بالـ slug للدور.
    """
    allowed = set(allowed_roles or [])

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user
            if not getattr(user, "is_authenticated", False):
                return redirect("reports:login")

            # السوبر دومًا مسموح
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
# صلاحيات أنواع التقارير (بالاعتماد على الدور + قسم المسؤول)
# ==============================
def allowed_categories_for(user) -> Set[str]:
    """
    يعيد مجموعة أكواد ReportType المسموحة للمستخدم:
      - {"all"} للسوبر أو الدور الذي يملك can_view_all_reports=True أو slug="manager".
      - اتحاد:
          • أكواد M2M على الدور: role.allowed_reporttypes (إن وُجدت)
          • أكواد reporttypes المربوطة بقسم المسؤول (إن كان Officer)
    ملاحظة: قد لا تستخدم في كل مكان، لأن بعض الشاشات تحتاج تقييدًا بكائنات ReportType
    نفسها؛ لكن تفيد في الفلاتر المبسطة.
    """
    try:
        if getattr(user, "is_superuser", False):
            return {"all"}

        role = _user_role(user)
        role_slug = _user_role_slug(user)
        if role_slug == "manager":
            return {"all"}

        if not role:
            return set()

        if getattr(role, "can_view_all_reports", False):
            return {"all"}

        allowed_codes: Set[str] = set()

        # أكواد من M2M على الدور (إن وُجد الحقل)
        try:
            codes_from_role = set(role.allowed_reporttypes.values_list("code", flat=True))
            allowed_codes |= {c for c in codes_from_role if c}
        except Exception:
            pass

        # أكواد من قسم المسؤول (إن كان Officer)
        dept = get_officer_department(user)
        if dept:
            try:
                codes_from_dept = set(dept.reporttypes.values_list("code", flat=True))
                allowed_codes |= {c for c in codes_from_dept if c}
            except Exception:
                pass

        return allowed_codes
    except Exception:
        return set()


# ==============================
# تقييد QuerySet بحسب المستخدم
# ==============================
def restrict_queryset_for_user(qs: QuerySet[Any], user) -> QuerySet[Any]:
    """
    يقيّد QuerySet للتقارير بحسب صلاحيات المستخدم:
      - السوبر/المدير: يرى الجميع.
      - المعلّم: يرى تقاريره فقط.
      - مسؤول القسم: أنواع التقارير المرتبطة بقسمه (Department.reporttypes).
      - أي دور آخر: أنواع التقارير المسموحة له عبر M2M على الدور.
    يعمل حتى لو كان حقل التصنيف إما FK إلى ReportType أو يحوي code عبر category__code.
    """
    # سوبر أو مدير: لا قيود
    role_slug = _user_role_slug(user)
    role = _user_role(user)
    if getattr(user, "is_superuser", False) or role_slug == "manager" or getattr(role, "can_view_all_reports", False):
        return qs

    # معلّم: تقاريره فقط
    if role_slug == "teacher":
        return qs.filter(teacher=user)

    # مسؤول القسم: قيد بأنواع تقارير القسم + ما يسمح به الدور (إن وُجد)
    dept = get_officer_department(user)
    allowed_codes = allowed_categories_for(user)

    if "all" in allowed_codes:
        return qs

    conditions = Q()

    # أنواع القسم (عبر FK مباشرة)
    if dept:
        try:
            dept_rts = dept.reporttypes.all()
            if dept_rts.exists():
                conditions |= Q(category__in=dept_rts) | Q(category_id__in=dept_rts.values_list("id", flat=True))
        except Exception:
            pass

    # أكواد من صلاحيات الدور
    if allowed_codes:
        conditions |= Q(category__code__in=list(allowed_codes))

    # لا يوجد شيء مسموح به → لا شيء
    if conditions == Q():
        return qs.none()

    return qs.filter(conditions).distinct()
