# reports/urls.py
from django.urls import path
from . import views

app_name = "reports"

urlpatterns = [
    # =========================
    # الدخول والخروج
    # =========================
    path("", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # =========================
    # الصفحة الرئيسية
    # =========================
    path("home/", views.home, name="home"),

    # =========================
    # التقارير (للمعلّم)
    # =========================
    path("reports/add/", views.add_report, name="add_report"),
    path("reports/my/", views.my_reports, name="my_reports"),
    path("reports/<int:pk>/edit/", views.edit_my_report, name="edit_my_report"),
    path("reports/<int:pk>/delete/", views.delete_my_report, name="delete_my_report"),

    # الطباعة والتصدير
    path("reports/<int:pk>/print/", views.report_print, name="report_print"),
    path("reports/<int:pk>/pdf/", views.report_pdf, name="report_pdf"),

    # =========================
    # تقارير الإدارة (Staff/Manager)
    # =========================
    path("reports/admin/", views.admin_reports, name="admin_reports"),
    path("reports/admin/<int:pk>/delete/", views.admin_delete_report, name="admin_delete_report"),

    # =========================
    # إدارة المعلّمين (للمدير)
    # =========================
    path("staff/teachers/", views.manage_teachers, name="manage_teachers"),
    path("staff/teachers/add/", views.add_teacher, name="add_teacher"),
    path("staff/teachers/<int:pk>/edit/", views.edit_teacher, name="edit_teacher"),
    path("staff/teachers/<int:pk>/delete/", views.delete_teacher, name="delete_teacher"),

    # =========================
    # إدارة الأقسام + التكليف
    # (اعتمدنا slug:code، ووفّرنا aliases للأسماء/المسارات القديمة)
    # =========================
    path("staff/departments/", views.departments_list, name="departments_list"),

    # إضافة قسم (اسم جديد + اسم قديم)
    path("staff/departments/add/", views.department_create, name="department_create"),
    path("staff/departments/add/", views.department_create, name="departments_add"),

    # تعديل بالقيمة الدلالية (slug/code)
    path("staff/departments/<slug:code>/edit/", views.department_edit, name="department_edit"),
    # توافق قديم (pk)
    path("staff/departments/<int:pk>/edit/", views.department_update, name="departments_edit"),

    # الأعضاء بالقيمة الدلالية
    path("staff/departments/<slug:code>/members/", views.department_members, name="department_members"),
    # توافق قديم (pk)
    path("staff/departments/<int:pk>/members/", views.department_members, name="departments_members"),

    # حذف بالقيمة الدلالية
    path("staff/departments/<slug:code>/delete/", views.department_delete, name="department_delete"),
    # توافق قديم (pk)
    path("staff/departments/<int:pk>/delete/", views.department_delete, name="departments_delete"),

    # =========================
    # لوحة المدير
    # =========================
    path("admin-dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("manager/", views.admin_dashboard, name="manager_dashboard"),

    # =========================
    # أنواع التقارير
    # =========================
    path("staff/report-types/", views.reporttypes_list, name="reporttypes_list"),
    path("staff/report-types/add/", views.reporttype_create, name="reporttype_create"),
    path("staff/report-types/<int:pk>/edit/", views.reporttype_update, name="reporttype_update"),
    path("staff/report-types/<int:pk>/delete/", views.reporttype_delete, name="reporttype_delete"),

    # =========================
    # التذاكر (Requests/Tickets)
    # =========================
    path("requests/new/", views.request_create, name="request_create"),
    path("requests/mine/", views.my_requests, name="my_requests"),
    path("requests/inbox/", views.tickets_inbox, name="tickets_inbox"),
    path("requests/assigned/", views.assigned_to_me, name="assigned_to_me"),
    path("requests/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("requests/admin/<int:pk>/", views.admin_request_update, name="admin_request_update"),
    path("officer/reports/", views.officer_reports, name="officer_reports"),

    # =========================
    # API
    # =========================
    path("api/department-members/", views.api_department_members, name="api_department_members"),
]
