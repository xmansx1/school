# reports/migrations/0003_create_report_table_if_missing.py
from django.db import migrations

def create_report_if_missing(apps, schema_editor):
    # يعمل على أي محرك (SQLite/Postgres/…)
    table_names = set(schema_editor.connection.introspection.table_names())
    if "reports_report" in table_names:
        return
    Report = apps.get_model("reports", "Report")
    # ينشئ الجدول كما هو معرّف في models.py
    schema_editor.create_model(Report)

class Migration(migrations.Migration):
    # ✅ عدّل اسم الهجرة السابقة هنا لما هو موجود عندك فعليًا
    # استخدم: python manage.py showmigrations reports
    dependencies = [
        ("reports", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(create_report_if_missing, migrations.RunPython.noop),
    ]
