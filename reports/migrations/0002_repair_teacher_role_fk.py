# reports/migrations/0002_repair_teacher_role_fk.py
from django.db import migrations

def repair_teacher_role_fk(apps, schema_editor):
    conn = schema_editor.connection
    vendor = conn.vendor
    cursor = conn.cursor()

    # === SQLite ===
    if vendor == "sqlite":
        # 1) إضافة العمود role_id إن لم يكن موجودًا
        def sqlite_column_exists(table, col):
            rows = cursor.execute(f'PRAGMA table_info("{table}")').fetchall()
            cols = [r[1] for r in rows]  # name at index 1
            return col in cols

        if not sqlite_column_exists("reports_teacher", "role_id"):
            cursor.execute('ALTER TABLE "reports_teacher" ADD COLUMN "role_id" INTEGER NULL')

        # 2) فهرس للأداء
        cursor.execute('CREATE INDEX IF NOT EXISTS "reports_teacher_role_id_idx" ON "reports_teacher" ("role_id")')

        # 3) زرع أدوار أساسية — بدون params لتجنّب TypeError في last_executed_query
        seeds = [
            ("manager",   "المدير",         1, 1),
            ("teacher",   "المعلم",         0, 0),
            ("activity",  "مسؤول النشاط",   1, 0),
            ("volunteer", "مسؤول التطوع",   1, 0),
            ("affairs",   "شؤون الطلاب",     1, 0),
            ("admin",     "إداري",           1, 0),
        ]
        def esc(s: str) -> str:
            # هروب بسيط للاقتباسات المفردة في SQLite
            return s.replace("'", "''")

        for slug, name, is_staff_by_default, can_view_all_reports in seeds:
            sql = (
                "INSERT OR IGNORE INTO \"reports_role\" "
                "(\"slug\",\"name\",\"is_staff_by_default\",\"can_view_all_reports\",\"is_active\") "
                f"VALUES ('{esc(slug)}','{esc(name)}',{int(is_staff_by_default)},{int(can_view_all_reports)},1)"
            )
            cursor.execute(sql)

        # ملاحظة: إضافة قيد FK لاحقًا في SQLite يتطلب إعادة بناء الجدول؛ نتجاوزها هنا
        # لأن نموذج Teacher يفرض العلاقة منطقيًا خلال ORM أثناء التطوير.

    # === PostgreSQL ===
    elif vendor == "postgresql":
        # 1) إضافة العمود إن لم يوجد
        cursor.execute("ALTER TABLE reports_teacher ADD COLUMN IF NOT EXISTS role_id INTEGER NULL")

        # 2) إضافة/تأكيد قيد الـ FK إن لم يوجد
        cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'reports_teacher_role_id_fk'
            ) THEN
                ALTER TABLE reports_teacher
                ADD CONSTRAINT reports_teacher_role_id_fk
                FOREIGN KEY (role_id) REFERENCES reports_role (id)
                ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
            END IF;
        END
        $$;
        """)

        # 3) فهرس
        cursor.execute("CREATE INDEX IF NOT EXISTS reports_teacher_role_id_idx ON reports_teacher (role_id)")

        # 4) زرع الأدوار
        cursor.execute("""
        INSERT INTO reports_role (slug, name, is_staff_by_default, can_view_all_reports, is_active)
        VALUES
          ('manager','المدير', TRUE, TRUE, TRUE),
          ('teacher','المعلم', FALSE, FALSE, TRUE),
          ('activity','مسؤول النشاط', TRUE, FALSE, TRUE),
          ('volunteer','مسؤول التطوع', TRUE, FALSE, TRUE),
          ('affairs','شؤون الطلاب', TRUE, FALSE, TRUE),
          ('admin','إداري', TRUE, FALSE, TRUE)
        ON CONFLICT (slug) DO NOTHING;
        """)

    # === Vendors أخرى (MySQL..): نحاول بلطف دون كسر ===
    else:
        try:
            cursor.execute("ALTER TABLE reports_teacher ADD COLUMN role_id INTEGER NULL")
        except Exception:
            pass
        try:
            cursor.execute("CREATE INDEX reports_teacher_role_id_idx ON reports_teacher (role_id)")
        except Exception:
            pass
        # زرع الأدوار يمكن تنفيذه بعبارات ثابتة كذلك
        base_sqls = [
            "INSERT IGNORE INTO reports_role (slug,name,is_staff_by_default,can_view_all_reports,is_active) VALUES ('manager','المدير',1,1,1)",
            "INSERT IGNORE INTO reports_role (slug,name,is_staff_by_default,can_view_all_reports,is_active) VALUES ('teacher','المعلم',0,0,1)",
            "INSERT IGNORE INTO reports_role (slug,name,is_staff_by_default,can_view_all_reports,is_active) VALUES ('activity','مسؤول النشاط',1,0,1)",
            "INSERT IGNORE INTO reports_role (slug,name,is_staff_by_default,can_view_all_reports,is_active) VALUES ('volunteer','مسؤول التطوع',1,0,1)",
            "INSERT IGNORE INTO reports_role (slug,name,is_staff_by_default,can_view_all_reports,is_active) VALUES ('affairs','شؤون الطلاب',1,0,1)",
            "INSERT IGNORE INTO reports_role (slug,name,is_staff_by_default,can_view_all_reports,is_active) VALUES ('admin','إداري',1,0,1)",
        ]
        for sql in base_sqls:
            try:
                cursor.execute(sql)
            except Exception:
                pass

def noop_reverse(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0001_initial"),
    ]
    operations = [
        migrations.RunPython(repair_teacher_role_fk, noop_reverse),
    ]
