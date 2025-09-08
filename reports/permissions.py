# reports/permissions.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import wraps
from typing import Iterable, Set, Any, Optional, List

from django.contrib import messages
from django.db.models import QuerySet, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import Department

# نحاول الاستيراد المرن لعضويات الأقسام
try:
    from .models import DepartmentMembership  # type: ignore
except Exception:  # pragma: no cover
    DepartmentMembership = None  # type: ignore

__all__ = [
    "get_officer_departments",
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
# اكتشاف “مسؤول قسم” (متعدد الأقسام)
# ==============================
def get_officer_departments(user) -> List[Department]:
    """
    يعيد قائمة الأقسام التي المستخدم مسؤول عنها:
      1) عبر DepartmentMembership.role_type = OFFICER (إن وُجد الموديل).
      2) fallback: مطابقة Department.slug == user.role.slug (أو role_label == role.name) للأقسام النشطة.
    تُعاد قائمة بدون تكرار ومحافظة على الترتيب.
    """
    if not getattr(user, "is_authenticated", False):
        return []

    seen = set()
    results: List[Department] = []

    # (1) عبر العضويات
    if DepartmentMembership is not None:
        try:
            memb_qs = (
                DepartmentMembership.objects.select_related("department")
                .filter(teacher=user, role_type=getattr(DepartmentMembership, "OFFICER", "officer"),
                        department__is_active=True)
            )
            for m in memb_qs:
                d = m.department
                if d and d.pk not in seen:
                    seen.add(d.pk)
                    results.append(d)
        except Exception:
            pass

    # (2) fallback عبر الدور
    try:
        role = _user_role(user)
        if role:
            qs = Department.objects.filter(is_active=True).only("id", "name", "slug")
            d = None
            if getattr(role, "slug", None):
                d = qs.filter(slug=role.slug).first()
            if not d and getattr(role, "name", None):
                d = qs.filter(role_label__iexact=role.name).first()
            if d and d.pk not in seen:
                results.append(d)
    except Exception:
        pass

    return results


def get_officer_department(user) -> Optional[Department]:
    """توافق خلفي: أول قسم من get_officer_departments أو None."""
    depts = get_officer_departments(user)
    return depts[0] if depts else None


def is_officer(user) -> bool:
    """هل المستخدم مسؤول قسم؟ (عضويات أو مطابقة الدور)"""
    return bool(get_officer_departments(user))


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
# صلاحيات أنواع التقارير (بالاعتماد على الدور + أقسام المسؤول)
# ==============================
def allowed_categories_for(user) -> Set[str]:
    """
    يعيد مجموعة أكواد ReportType المسموحة للمستخدم:
      - {"all"} للسوبر أو الدور الذي يملك can_view_all_reports=True أو slug="manager".
      - اتحاد:
          • أكواد M2M على الدور: role.allowed_reporttypes
          • أكواد reporttypes لكل الأقسام التي هو مسؤول عنها عبر DepartmentMembership
    """
    try:
        # سوبر ومدير أو دور يرى الكل
        if getattr(user, "is_superuser", False):
            return {"all"}
        role = _user_role(user)
        role_slug = _user_role_slug(user)
        if role_slug == "manager":
            return {"all"}
        if role and getattr(role, "can_view_all_reports", False):
            return {"all"}

        allowed_codes: Set[str] = set()

        # من الدور (إن وُجد الحقل)
        try:
            if role:
                allowed_codes |= set(c for c in role.allowed_reporttypes.values_list("code", flat=True) if c)
        except Exception:
            pass

        # من جميع أقسام المسؤول
        try:
            for d in get_officer_departments(user):
                allowed_codes |= set(c for c in d.reporttypes.values_list("code", flat=True) if c)
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
      - السوبر/المدير/الدور الذي يرى الكل: يرى الجميع.
      - غير ذلك: يرى تقاريره + أي تقرير يقع ضمن الأنواع المسموح بها له (من الدور/الأقسام).
    """
    role = _user_role(user)
    role_slug = _user_role_slug(user)

    # سوبر أو مدير أو can_view_all_reports: لا قيود
    if getattr(user, "is_superuser", False) or role_slug == "manager" or (role and getattr(role, "can_view_all_reports", False)):
        return qs

    allowed_codes = allowed_categories_for(user)
    if "all" in allowed_codes:
        return qs

    conditions = Q(teacher=user)  # دائمًا يرى تقاريره
    if allowed_codes:
        conditions |= Q(category__code__in=list(allowed_codes))

    return qs.filter(conditions).distinct()
