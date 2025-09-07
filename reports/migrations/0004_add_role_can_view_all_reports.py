# reports/migrations/0004_add_role_can_view_all_reports.py
from django.db import migrations

def add_can_view_all_reports(apps, schema_editor):
    conn = schema_editor.connection
    vendor = conn.vendor
    cursor = conn.cursor()

    # إذا كان العمود موجودًا بالفعل، لا نفعل شيئًا
    def column_exists_sqlite(table, col):
        rows = cursor.execute(f'PRAGMA table_info("{table}")').fetchall()
        cols = [r[1] for r in rows]  # name at index 1
        return col in cols

    if vendor == "sqlite":
        if not column_exists_sqlite("reports_role", "can_view_all_reports"):
            cursor.execute('ALTER TABLE "reports_role" ADD COLUMN "can_view_all_reports" BOOLEAN NOT NULL DEFAULT 0')
        # لا حاجة لشيء آخر
    elif vendor == "postgresql":
        # في Postgres نستخدم IF NOT EXISTS بشكل آمن
        cursor.execute("""
            ALTER TABLE reports_role
            ADD COLUMN IF NOT EXISTS can_view_all_reports BOOLEAN NOT NULL DEFAULT FALSE
        """)
    else:
        # Vendors أخرى: نحاول بلطف بدون كسر
        try:
            cursor.execute("ALTER TABLE reports_role ADD COLUMN can_view_all_reports BOOLEAN NOT NULL DEFAULT 0")
        except Exception:
            pass

def noop_reverse(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0003_repair_ticket_department_fk"),
    ]
    operations = [
        migrations.RunPython(add_can_view_all_reports, noop_reverse),
    ]
