# reports/context_processors.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Any, List, Iterable
from datetime import timedelta
from django.http import HttpRequest
from django.utils import timezone
from django.apps import apps

from .models import Ticket, Department, Report

# حالات الطلبات (توافق قديم/جديد)
OPEN_STATES = {"open", "new"}
INPROGRESS_STATES = {"in_progress", "pending"}
UNRESOLVED_STATES = OPEN_STATES | INPROGRESS_STATES
CLOSED_STATES = {"done", "rejected", "cancelled"}


# =========================
# مساعدات آمنة
# =========================
def _safe_count(qs) -> int:
    """عدّ آمن بدون كسر الهيدر لو حدث خطأ."""
    try:
        return qs.only("id").count()
    except Exception:
        return 0


def _get_membership_model():
    """جلب موديل DepartmentMembership إن وُجد."""
    try:
        return apps.get_model("reports", "DepartmentMembership")
    except Exception:
        return None


def _officer_role_values(membership_model) -> Iterable:
    """
    إرجاع القيم المحتملة لتمثيل 'OFFICER' لدعم أكثر من بنية (سلاسل/أرقام/Enum).
    """
    values = set()
    if membership_model is None:
        return {"officer", 1, "1"}
    # ثابت علوي مثل DepartmentMembership.OFFICER
    v = getattr(membership_model, "OFFICER", None)
    if v is not None:
        values.add(v)
    # نمط Enum داخلي DepartmentMembership.RoleType.OFFICER
    RoleType = getattr(membership_model, "RoleType", None)
    if RoleType is not None:
        v = getattr(RoleType, "OFFICER", None)
        if v is not None:
            values.add(v)
    # قيَم افتراضية احتياطية
    values.update({"officer", 1, "1"})
    return values


# =========================
# اكتشاف أقسام المسؤول (عضوية فقط)
# =========================
def _detect_officer_departments(user) -> List[Department]:
    """
    يُعتبر المستخدم مسؤول قسم فقط إذا كانت لديه عضوية DepartmentMembership
    بقيمة role_type = OFFICER على قسم فعّال. (لا يعتمد على role.slug أو allowed_reporttypes)
    """
    Membership = _get_membership_model()
    if Membership is None:
        return []

    try:
        officer_values = list(_officer_role_values(Membership))
        membs = (
            Membership.objects.select_related("department")
            .filter(teacher=user, role_type__in=officer_values, department__is_active=True)
        )
        # إزالة التكرار مع الحفاظ على الترتيب
        seen, unique = set(), []
        for m in membs:
            d = m.department
            if d and d.pk not in seen:
                seen.add(d.pk)
                unique.append(d)
        return unique
    except Exception:
        return []


# =========================
# المتغيرات الممرّرة للقوالب
# =========================
def nav_context(request: HttpRequest) -> Dict[str, Any]:
    """
    يزوّد القوالب بمتغيرات الهيدر:
      - NAV_MY_OPEN_TICKETS, NAV_ASSIGNED_TO_ME
      - IS_OFFICER, OFFICER_DEPARTMENT (أول قسم)، OFFICER_DEPARTMENTS
      - SHOW_OFFICER_REPORTS_LINK  ← يظهر فقط لمن لديهم عضوية OFFICER (ولا يظهر للـ superuser ما لم يكن Officer)
      - NAV_OFFICER_REPORTS: عدد تقارير أقسامه خلال آخر 7 أيام
    """
    u = getattr(request, "user", None)
    if not u or not getattr(u, "is_authenticated", False):
        return {
            "NAV_MY_OPEN_TICKETS": 0,
            "NAV_ASSIGNED_TO_ME": 0,
            "IS_OFFICER": False,
            "OFFICER_DEPARTMENT": None,
            "OFFICER_DEPARTMENTS": [],
            "SHOW_OFFICER_REPORTS_LINK": False,
            "NAV_OFFICER_REPORTS": 0,
        }

    # العدادات
    my_open = _safe_count(Ticket.objects.filter(creator=u, status__in=UNRESOLVED_STATES))
    assigned_open = _safe_count(Ticket.objects.filter(assignee=u, status__in=UNRESOLVED_STATES))

    # أقسام المسؤول (عضوية OFFICER فعّالة فقط)
    officer_depts = _detect_officer_departments(u)
    is_officer = bool(officer_depts)

    # بادج "تقارير قسمي": عدد تقارير جميع الأقسام المرتبطة خلال 7 أيام
    nav_officer_reports = 0
    if is_officer:
        try:
            start = timezone.localdate() - timedelta(days=7)
            rt_ids = set()
            for d in officer_depts:
                try:
                    rt_ids.update(d.reporttypes.values_list("id", flat=True))
                except Exception:
                    # في حال عدم وجود العلاقة reporttypes
                    pass
            if rt_ids:
                nav_officer_reports = Report.objects.filter(
                    category_id__in=list(rt_ids),
                    report_date__gte=start,
                ).count()
        except Exception:
            nav_officer_reports = 0

    return {
        "NAV_MY_OPEN_TICKETS": my_open,
        "NAV_ASSIGNED_TO_ME": assigned_open,
        "IS_OFFICER": is_officer,
        "OFFICER_DEPARTMENT": officer_depts[0] if officer_depts else None,
        "OFFICER_DEPARTMENTS": officer_depts,
        # ↓↓↓ أخفِ الرابط عمّن ليس مسؤول قسم ↓↓↓
        "SHOW_OFFICER_REPORTS_LINK": is_officer,
        "NAV_OFFICER_REPORTS": nav_officer_reports,
    }


# توافق خلفي: إن كانت الإعدادات ما زالت تستدعي nav_counters أو nav_badges
def nav_counters(request: HttpRequest) -> Dict[str, int]:
    ctx = nav_context(request)
    return {
        "NAV_MY_OPEN_TICKETS": int(ctx.get("NAV_MY_OPEN_TICKETS", 0)),
        "NAV_ASSIGNED_TO_ME": int(ctx.get("NAV_ASSIGNED_TO_ME", 0)),
    }


def nav_badges(request: HttpRequest) -> Dict[str, Any]:
    """توافق كامل مع أي إعدادات قديمة تشير إلى nav_badges."""
    return nav_context(request)


__all__ = ["nav_context", "nav_counters", "nav_badges"]
