# reports/migrations/0003_repair_ticket_department_fk.py
from django.db import migrations

def repair_ticket_department_fk(apps, schema_editor):
    conn = schema_editor.connection
    vendor = conn.vendor
    cursor = conn.cursor()

    # الهدف:
    # 1) إضافة العمود department_id إلى reports_ticket إن لم يكن موجودًا (NULLABLE).
    # 2) إنشاء فهرس للعمود.
    # 3) تفعيل FK إلى reports_department في Postgres فقط (SQLite يحتاج إعادة بناء الجدول لإضافة FK لاحقًا).
    #
    # ملاحظة: نعتمد على أسماء الجداول الافتراضية من Django:
    #   reports_ticket (الحقل الجديد department_id INTEGER/INT)
    #   reports_department (المرجع)

    if vendor == "sqlite":
        # --- SQLite ---
        # helper: check column exists
        def sqlite_column_exists(table, col):
            rows = cursor.execute(f'PRAGMA table_info("{table}")').fetchall()
            cols = [r[1] for r in rows]  # name at index 1
            return col in cols

        # 1) add column if missing
        if not sqlite_column_exists("reports_ticket", "department_id"):
            cursor.execute('ALTER TABLE "reports_ticket" ADD COLUMN "department_id" INTEGER NULL')

        # 2) index (safe if exists)
        cursor.execute('CREATE INDEX IF NOT EXISTS "reports_ticket_department_id_idx" ON "reports_ticket" ("department_id")')

        # 3) no FK add (requires table rebuild in SQLite) — نتجاوزه لبيئة التطوير

    elif vendor == "postgresql":
        # --- PostgreSQL ---
        # 1) add column if missing
        cursor.execute("ALTER TABLE reports_ticket ADD COLUMN IF NOT EXISTS department_id INTEGER NULL")

        # 2) add FK constraint if missing
        cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'reports_ticket_department_id_fk'
            ) THEN
                ALTER TABLE reports_ticket
                ADD CONSTRAINT reports_ticket_department_id_fk
                FOREIGN KEY (department_id) REFERENCES reports_department (id)
                ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END
        $$;
        """)

        # 3) index
        cursor.execute("CREATE INDEX IF NOT EXISTS reports_ticket_department_id_idx ON reports_ticket (department_id)")

    else:
        # --- Other vendors (e.g., MySQL) — best-effort without breaking ---
        try:
            cursor.execute("ALTER TABLE reports_ticket ADD COLUMN department_id INTEGER NULL")
        except Exception:
            pass
        try:
            cursor.execute("CREATE INDEX reports_ticket_department_id_idx ON reports_ticket (department_id)")
        except Exception:
            pass
        # FK optional here

def noop_reverse(apps, schema_editor):
    # لا حاجة لعكس — آمن
    pass

class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0002_repair_teacher_role_fk"),
    ]
    operations = [
        migrations.RunPython(repair_ticket_department_fk, noop_reverse),
    ]
