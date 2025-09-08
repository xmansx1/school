# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Any, List, Iterable, Optional, Tuple, Set
from datetime import timedelta
from django.http import HttpRequest
from django.utils import timezone
from django.apps import apps
from django.db.models import Q
from django.urls import reverse

from .models import Ticket, Department, Report

# حالات التذاكر
OPEN_STATES = {"open", "new"}
INPROGRESS_STATES = {"in_progress", "pending"}
UNRESOLVED_STATES = OPEN_STATES | INPROGRESS_STATES
CLOSED_STATES = {"done", "rejected", "cancelled"}


# -----------------------------
# أدوات مساعدة عامة
# -----------------------------
def _safe_count(qs) -> int:
    try:
        return qs.only("id").count()
    except Exception:
        return 0


def _get_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except Exception:
        return None


def _get_membership_model():
    return _get_model("reports", "DepartmentMembership")


def _model_fields(model) -> Set[str]:
    try:
        return {f.name for f in model._meta.get_fields()}
    except Exception:
        return set()


# -----------------------------
# كشف أقسام المسؤول Officer
# -----------------------------
def _officer_role_values(membership_model) -> Iterable:
    values = set()
    if membership_model is None:
        return {"officer", 1, "1"}
    v = getattr(membership_model, "OFFICER", None)
    if v is not None:
        values.add(v)
    RoleType = getattr(membership_model, "RoleType", None)
    if RoleType is not None:
        v = getattr(RoleType, "OFFICER", None)
        if v is not None:
            values.add(v)
    # fallback
    values.update({"officer", 1, "1"})
    return values


def _detect_officer_departments(user) -> List[Department]:
    Membership = _get_membership_model()
    if Membership is None:
        return []
    try:
        officer_values = list(_officer_role_values(Membership))
        membs = (
            Membership.objects.select_related("department")
            .filter(teacher=user, role_type__in=officer_values, department__is_active=True)
        )
        seen, unique = set(), []
        for m in membs:
            d = m.department
            if d and d.pk not in seen:
                seen.add(d.pk)
                unique.append(d)
        return unique
    except Exception:
        return []


def _user_department_codes(user) -> List[str]:
    Membership = _get_membership_model()
    if Membership is None:
        return []
    try:
        codes = list(
            Membership.objects.filter(teacher=user, department__is_active=True)
            .values_list("department__slug", flat=True)
        )
        return [c for c in codes if c]
    except Exception:
        return []


# -----------------------------
# نماذج الإشعارات (ديناميكيًا)
# -----------------------------
def _notification_models():
    """يُعيد موديل الإشعار + موديل سجل الاستلام/القراءة إن وُجد."""
    N = (
        _get_model("reports", "Notification")
        or _get_model("reports", "Announcement")
        or _get_model("reports", "AdminMessage")
    )
    # ✅ دعم اسم NotificationRecipient (المعمول به في مشروعك)
    R = (
        _get_model("reports", "NotificationRecipient")
        or _get_model("reports", "NotificationRead")
        or _get_model("reports", "NotificationReceipt")
        or _get_model("reports", "NotificationSeen")
    )
    return N, R


def _notification_sender_str(obj) -> str:
    f = _model_fields(obj.__class__)
    for cand in ("sender", "created_by", "author", "user", "teacher", "owner"):
        if cand in f:
            try:
                v = getattr(obj, cand, None)
                if v:
                    return str(
                        getattr(v, "name", None)
                        or getattr(v, "phone", None)
                        or getattr(v, "username", None)
                        or v
                    )
            except Exception:
                pass
    return "الإدارة"


def _exclude_notif_dismissed_cookies_notif_qs(qs, request: Optional[HttpRequest]):
    """استبعاد الإشعارات التي أخفاها المستخدم عبر الكوكي على مستوى Notification."""
    if not request:
        return qs
    try:
        ids = list(qs.values_list("id", flat=True)[:80])
        skip = [i for i in ids if request.COOKIES.get(f"notif_dismissed_{i}")]
        return qs.exclude(id__in=skip) if skip else qs
    except Exception:
        return qs


def _exclude_notif_dismissed_cookies_recipient_qs(qs, request: Optional[HttpRequest], notif_fk: str):
    """استبعاد سجلات الاستلام التي أخفاها المستخدم عبر الكوكي (يفترض وجود FK اسمه notif_fk)."""
    if not request:
        return qs
    try:
        ids = list(qs.values_list(f"{notif_fk}_id", flat=True)[:80])
        skip = [i for i in ids if request.COOKIES.get(f"notif_dismissed_{i}")]
        return qs.exclude(**{f"{notif_fk}_id__in": skip}) if skip else qs
    except Exception:
        return qs


def _published_notifications_qs(N):
    """فلترة نشر/نشاط/فترات زمنية على مستوى Notification."""
    qs = N.objects.all()
    now = timezone.now()
    f = _model_fields(N)
    try:
        if "is_active" in f:
            qs = qs.filter(is_active=True)
        if "status" in f and hasattr(N, "Status"):
            try:
                published_value = getattr(N.Status, "PUBLISHED", None)
                if published_value is not None:
                    qs = qs.filter(status=published_value)
            except Exception:
                pass
        # حقول أوقات شائعة
        if "starts_at" in f:
            qs = qs.filter(Q(starts_at__lte=now) | Q(starts_at__isnull=True))
        if "ends_at" in f:
            qs = qs.filter(Q(ends_at__gte=now) | Q(ends_at__isnull=True))
        if "publish_at" in f:
            qs = qs.filter(Q(publish_at__lte=now) | Q(publish_at__isnull=True))
        if "expires_at" in f:
            qs = qs.filter(Q(expires_at__gte=now) | Q(expires_at__isnull=True))
    except Exception:
        pass
    return qs


def _targeted_for_user_q(N, user) -> Q:
    """
    استهداف المستخدم مباشرة من موديل Notification (Fallback فقط).
    مشروعك يعتمد NotificationRecipient لذا هذا المسار يُستخدم فقط إذا لم يتوفر R.
    """
    f = _model_fields(N)
    q = Q()
    if "teacher" in f:
        q |= Q(teacher=user)
    if "user" in f:
        q |= Q(user=user)
    for m2m_name in ("recipients", "teachers", "users", "audience_teachers"):
        if m2m_name in f:
            try:
                q |= Q(**{f"{m2m_name}": user})
            except Exception:
                pass
    user_codes = _user_department_codes(user)
    if user_codes:
        if "department" in f:
            q |= Q(department__slug__in=user_codes) | Q(department__code__in=user_codes)
        if "departments" in f:
            q |= Q(departments__slug__in=user_codes) | Q(departments__code__in=user_codes)
    if "is_broadcast" in f:
        q |= Q(is_broadcast=True)
    return q


def _order_newest(qs, N_or_R):
    f = _model_fields(N_or_R)
    order_fields = []
    for cand in ("created_at", "created_on", "publish_at", "starts_at", "id"):
        if cand in f:
            order_fields.append(f"-{cand}")
    if order_fields:
        try:
            return qs.order_by(*order_fields)
        except Exception:
            pass
    return qs


def _notification_title_body_dict(obj) -> Tuple[str, str]:
    f = _model_fields(obj.__class__)
    title = ""
    for cand in ("title", "subject", "heading", "name"):
        if cand in f:
            try:
                title = getattr(obj, cand) or ""
                break
            except Exception:
                pass
    body = ""
    for cand in ("body", "message", "content", "text", "details"):
        if cand in f:
            try:
                body = getattr(obj, cand) or ""
                break
            except Exception:
                pass
    return (str(title).strip() or "إشعار"), str(body or "")


def _build_hero_payload_from_notification(n) -> Dict[str, Any]:
    title, body = _notification_title_body_dict(n)
    data: Dict[str, Any] = {
        "id": getattr(n, "pk", None),
        "title": title,
        "body": body,
        "sender_name": _notification_sender_str(n),
    }
    f = _model_fields(n.__class__)
    for cand in ("action_url", "url", "link"):
        if cand in f:
            try:
                data["action_url"] = getattr(n, cand) or ""
                break
            except Exception:
                pass
    return data


def _pick_hero_notification(user, request: Optional[HttpRequest] = None) -> Optional[Dict[str, Any]]:
    """
    يُعيد حمولة نافذة هيرو المنبثقة:
    - أولاً عبر NotificationRecipient (غير مقروء → أحدث)،
    - وإلا فالباك عبر Notification موجه للمستخدم (إن وُجد).
    """
    N, R = _notification_models()
    if not N:
        return None

    # المسار المفضل: عبر سجلات الاستلام (Recipient)
    if R:
        fR = _model_fields(R)

        # اكتشاف أسماء الحقول
        notif_fk = None
        for cand in ("notification", "notif", "message"):
            if cand in fR:
                notif_fk = cand
                break
        user_fk = None
        for cand in ("teacher", "user", "recipient"):
            if cand in fR:
                user_fk = cand
                break

        if notif_fk and user_fk:
            try:
                now = timezone.now()
                qs = R.objects.select_related(notif_fk)

                # فلترة تخصّص المستلم
                qs = qs.filter(**{user_fk: user})

                # غير مقروء
                if "is_read" in fR:
                    qs = qs.filter(is_read=False)
                elif "read_at" in fR:
                    qs = qs.filter(Q(read_at__isnull=True))

                # استبعاد المنتهي/غير المنشور عبر FK إلى Notification
                fN = _model_fields(N)
                if "expires_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__expires_at__gt": now}) | qs.filter(
                        **{f"{notif_fk}__expires_at__isnull": True}
                    )
                if "is_active" in fN:
                    qs = qs.filter(**{f"{notif_fk}__is_active": True})
                if "publish_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__publish_at__lte": now}) | qs.filter(
                        **{f"{notif_fk}__publish_at__isnull": True}
                    )
                if "starts_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__starts_at__lte": now}) | qs.filter(
                        **{f"{notif_fk}__starts_at__isnull": True}
                    )
                if "ends_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__ends_at__gte": now}) | qs.filter(
                        **{f"{notif_fk}__ends_at__isnull": True}
                    )

                # استبعاد الكوكي (Dismiss)
                qs = _exclude_notif_dismissed_cookies_recipient_qs(qs, request, notif_fk)

                # ترتيب بالأحدث الممكن
                qs = _order_newest(qs, R)

                rec = qs.first()
                if rec:
                    try:
                        n = getattr(rec, notif_fk)
                    except Exception:
                        n = None
                    if n:
                        return _build_hero_payload_from_notification(n)
            except Exception:
                pass

    # فالباك: مباشرة من Notification (يعمل فقط إن كان هناك استهداف عبر حقول الـ Notification نفسها)
    try:
        now = timezone.now()
        base_qs = _published_notifications_qs(N).filter(_targeted_for_user_q(N, user)).distinct()
        base_qs = _exclude_notif_dismissed_cookies_notif_qs(base_qs, request)
        base_qs = _order_newest(base_qs, N)

        obj = base_qs.only("id")[:1].first()
        if obj:
            return _build_hero_payload_from_notification(obj)

        # فرصة ثانية: نطاق آخر 3 أيام
        try:
            fN = _model_fields(N)
            recent_qs = _published_notifications_qs(N).filter(_targeted_for_user_q(N, user)).distinct()
            three_days_ago = now - timedelta(days=3)
            if "created_at" in fN:
                recent_qs = recent_qs.filter(created_at__gte=three_days_ago)
            elif "created_on" in fN:
                recent_qs = recent_qs.filter(created_on__gte=three_days_ago)
            elif "publish_at" in fN:
                recent_qs = recent_qs.filter(publish_at__gte=three_days_ago)
            recent_qs = _exclude_notif_dismissed_cookies_notif_qs(recent_qs, request)
            recent_qs = _order_newest(recent_qs, N)
            obj = recent_qs.only("id")[:1].first()
            if obj:
                return _build_hero_payload_from_notification(obj)
        except Exception:
            pass
    except Exception:
        pass

    return None


def _unread_count(user) -> int:
    """عدد الإشعارات غير المقروءة للمستخدم."""
    N, R = _notification_models()
    if not N:
        return 0

    # المسار المفضل: NotificationRecipient
    if R:
        try:
            fR = _model_fields(R)
            user_fk = None
            for cand in ("teacher", "user", "recipient"):
                if cand in fR:
                    user_fk = cand
                    break
            if not user_fk:
                return 0

            qs = R.objects.filter(**{user_fk: user})
            if "is_read" in fR:
                qs = qs.filter(is_read=False)
            elif "read_at" in fR:
                qs = qs.filter(read_at__isnull=True)

            # استبعاد المنتهي عبر FK إن أمكن
            notif_fk = None
            for cand in ("notification", "notif", "message"):
                if cand in fR:
                    notif_fk = cand
                    break
            if notif_fk:
                fN = _model_fields(N)
                now = timezone.now()
                if "expires_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__expires_at__gt": now}) | qs.filter(
                        **{f"{notif_fk}__expires_at__isnull": True}
                    )
                if "is_active" in fN:
                    qs = qs.filter(**{f"{notif_fk}__is_active": True})

            return _safe_count(qs)
        except Exception:
            return 0

    # فالباك: بلا سجل استلام → نعجز عن قياس غير المقروء بدقة
    try:
        qs = _published_notifications_qs(N).filter(_targeted_for_user_q(N, user)).distinct()
        return _safe_count(qs)
    except Exception:
        return 0


def _reverse_any(names: Iterable[str]) -> Optional[str]:
    for n in names:
        try:
            return reverse(n)
        except Exception:
            continue
    return None


# -----------------------------
# المُعالج الرئيس لكونتكست التنقل
# -----------------------------
# ... (باقي الاستيرادات والدوال كما لديك تمامًا)

def nav_context(request: HttpRequest) -> Dict[str, Any]:
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
            "SHOW_ADMIN_DASHBOARD_LINK": False,
            "NAV_NOTIFICATIONS_UNREAD": 0,
            "NAV_NOTIFICATION_HERO": None,
            "CAN_SEND_NOTIFICATIONS": False,
            "SEND_NOTIFICATION_URL": None,
        }

    try:
        my_open = _safe_count(Ticket.objects.filter(creator=u, status__in=UNRESOLVED_STATES))
    except Exception:
        my_open = 0
    try:
        assigned_open = _safe_count(Ticket.objects.filter(assignee=u, status__in=UNRESOLVED_STATES))
    except Exception:
        assigned_open = 0

    officer_depts = _detect_officer_departments(u)
    is_officer = bool(officer_depts)
    show_officer_link = bool(getattr(u, "is_superuser", False) or is_officer)

    # تقارير officer
    nav_officer_reports = 0
    try:
        start_date = timezone.localdate() - timedelta(days=7)
        if getattr(u, "is_superuser", False):
            nav_officer_reports = Report.objects.filter(report_date__gte=start_date).count()
        elif is_officer:
            rt_ids: set = set()
            rt_slugs: set = set()
            for d in officer_depts:
                try:
                    rt_ids.update(d.reporttypes.values_list("id", flat=True))
                except Exception:
                    pass
                try:
                    rt_slugs.update(d.reporttypes.values_list("slug", flat=True))
                except Exception:
                    pass
            if rt_ids or rt_slugs:
                q = Report.objects.filter(report_date__gte=start_date)
                if rt_ids:
                    try:
                        nav_officer_reports = q.filter(category_id__in=list(rt_ids)).count()
                    except Exception:
                        pass
                if not nav_officer_reports and rt_slugs:
                    try:
                        nav_officer_reports = q.filter(category__in=list(rt_slugs)).count()
                    except Exception:
                        nav_officer_reports = 0
    except Exception:
        nav_officer_reports = 0

    # روابط لوحة المدير
    try:
        role_slug = getattr(getattr(u, "role", None), "slug", None)
        show_admin_link = bool(getattr(u, "is_superuser", False) or role_slug == "manager")
    except Exception:
        role_slug = None
        show_admin_link = bool(getattr(u, "is_superuser", False))

    # من يحق له إرسال إشعارات؟
    can_send_notifications = bool(getattr(u, "is_superuser", False) or role_slug == "manager" or is_officer)

    # اختر الرابط الأنسب الذي يملك إذن الدخول إليه
    send_notification_url = None
    if can_send_notifications:
        # نفضّل المسار المحمي الموحّد
        send_notification_url = _reverse_any([
            "reports:notifications_create",   # يسمح للمدير/المسؤول (بعد تعديل الديكوريتر)
            "reports:send_notification",      # fallback قديم
            "reports:notification_create",
            "reports:announcement_create",
            "reports:admin_message_create",
            "reports:notifications_send",
        ])

    # عداد الإشعارات + الـ Hero
    try:
        unread_count = _unread_count(u)
    except Exception:
        unread_count = 0
    try:
        hero = _pick_hero_notification(u, request=request)
    except Exception:
        hero = None

    return {
        "NAV_MY_OPEN_TICKETS": my_open,
        "NAV_ASSIGNED_TO_ME": assigned_open,
        "IS_OFFICER": is_officer,
        "OFFICER_DEPARTMENT": officer_depts[0] if officer_depts else None,
        "OFFICER_DEPARTMENTS": officer_depts,
        "SHOW_OFFICER_REPORTS_LINK": show_officer_link,
        "NAV_OFFICER_REPORTS": nav_officer_reports,
        "SHOW_ADMIN_DASHBOARD_LINK": show_admin_link,
        "NAV_NOTIFICATIONS_UNREAD": unread_count,
        "NAV_NOTIFICATION_HERO": hero,
        "CAN_SEND_NOTIFICATIONS": can_send_notifications,
        "SEND_NOTIFICATION_URL": send_notification_url,
    }


def nav_counters(request: HttpRequest) -> Dict[str, int]:
    ctx = nav_context(request)
    return {
        "NAV_MY_OPEN_TICKETS": int(ctx.get("NAV_MY_OPEN_TICKETS", 0)),
        "NAV_ASSIGNED_TO_ME": int(ctx.get("NAV_ASSIGNED_TO_ME", 0)),
    }


def nav_badges(request: HttpRequest) -> Dict[str, Any]:
    return nav_context(request)


__all__ = ["nav_context", "nav_counters", "nav_badges"]
