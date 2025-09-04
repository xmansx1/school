# reports/context_processors.py
from __future__ import annotations

from typing import Dict
from django.http import HttpRequest

from .models import Ticket


# حالات غير منجزة (تظهر كعدادات في الهيدر)
# ندعم التسميات القديمة والجديدة لضمان التوافق:
OPEN_STATES = {"open", "new"}
INPROGRESS_STATES = {"in_progress", "pending"}
UNRESOLVED_STATES = OPEN_STATES | INPROGRESS_STATES

# حالات منتهية/مغلقة
CLOSED_STATES = {"done", "rejected", "cancelled"}


def nav_counters(request: HttpRequest) -> Dict[str, int]:
    """
    يضيف عدادات للتنقل في الهيدر:
      - NAV_MY_OPEN_TICKETS: عدد طلبات المستخدم (creator) غير المنجزة
      - NAV_ASSIGNED_TO_ME: عدد الطلبات المعيّنة للمستخدم (assignee) وغير المنجزة
    ملاحظة: نستخدم مجموع حالات "غير منجز" بدل الاستثناء من "منجز/مرفوض" فقط،
    لدعم حالات قديمة مثل pending/new إن وُجدت في قاعدة البيانات.
    """
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}

    user = request.user
    try:
        my_open = Ticket.objects.filter(
            creator=user,
            status__in=UNRESOLVED_STATES,
        ).count()
    except Exception:
        # لا نكسر الهيدر لو حدثت مشكلة عرضية
        my_open = 0

    try:
        assigned_open = Ticket.objects.filter(
            assignee=user,
            status__in=UNRESOLVED_STATES,
        ).count()
    except Exception:
        assigned_open = 0

    return {
        "NAV_MY_OPEN_TICKETS": my_open,
        "NAV_ASSIGNED_TO_ME": assigned_open,
    }
