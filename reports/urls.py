# reports/urls.py
from django.urls import path
from . import views

app_name = "reports"

urlpatterns = [
    # تسجيل الدخول والخروج
    path("", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # الصفحة الرئيسية
    path("home/", views.home, name="home"),

    # تقارير المعلم
    path("add/", views.add_report, name="add_report"),
    path("my-reports/", views.my_reports, name="my_reports"),

    # تقارير المدير
    path("admin-reports/", views.admin_reports, name="admin_reports"),
    path("admin-reports/<int:pk>/delete/", views.admin_delete_report, name="admin_delete_report"),

    # الطباعة/PDF
    path("report/<int:pk>/print/", views.report_print, name="report_print"),
    path("report/<int:pk>/pdf/", views.report_pdf, name="report_pdf"),

    # إدارة المعلمين (لا تبدأ بـ admin/ حتى لا تتصادم مع Django admin)
    path("staff/teachers/", views.manage_teachers, name="manage_teachers"),
    path("staff/teachers/add/", views.add_teacher, name="add_teacher"),
    path("staff/teachers/<int:pk>/edit/", views.edit_teacher, name="edit_teacher"),
    path("staff/teachers/<int:pk>/delete/", views.delete_teacher, name="delete_teacher"),
]
