# reports/context_processors.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict
from django.http import HttpRequest

from .models import Ticket

__all__ = ["nav_counters"]

# حالات غير منجزة (تظهر كعدادات في الهيدر)
# ندعم التسميات القديمة والجديدة لضمان التوافق:
OPEN_STATES = {"open", "new"}
INPROGRESS_STATES = {"in_progress", "pending"}
UNRESOLVED_STATES = OPEN_STATES | INPROGRESS_STATES

# حالات منتهية/مغلقة (للمرجع فقط)
CLOSED_STATES = {"done", "rejected", "cancelled"}


def nav_counters(request: HttpRequest) -> Dict[str, int]:
    """
    يضيف عدادات للتنقل في الهيدر:
      - NAV_MY_OPEN_TICKETS: عدد طلبات المستخدم (creator) غير المنجزة
      - NAV_ASSIGNED_TO_ME: عدد الطلبات المعيّنة للمستخدم (assignee) وغير المنجزة
    نستخدم مجموعة حالات "غير منجز" لدعم أية بيانات قديمة (مثل pending/new) إن وُجدت.
    لا يرفع استثناءات حتى لا يتعطل الهيدر.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    try:
        my_open = (
            Ticket.objects.filter(creator=user, status__in=UNRESOLVED_STATES)
            .only("id")
            .count()
        )
    except Exception:
        my_open = 0

    try:
        assigned_open = (
            Ticket.objects.filter(assignee=user, status__in=UNRESOLVED_STATES)
            .only("id")
            .count()
        )
    except Exception:
        assigned_open = 0

    return {
        "NAV_MY_OPEN_TICKETS": my_open,
        "NAV_ASSIGNED_TO_ME": assigned_open,
    }
