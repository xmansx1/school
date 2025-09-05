# reports/migrations/0002_repair_teacher_role_fk.py
from django.db import migrations

# 1) إضافة role_id إن لم يوجد
ADD_ROLE_ID_COL_SQL = r"""
ALTER TABLE reports_teacher
ADD COLUMN IF NOT EXISTS role_id INTEGER NULL;
"""

# 2) إضافة/تأكيد قيد الـ FK على role_id إن لم يكن موجودًا
ADD_ROLE_FK_SQL = r"""
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
"""

# 3) فهرس للأداء
ADD_ROLE_ID_INDEX_SQL = r"""
CREATE INDEX IF NOT EXISTS reports_teacher_role_id_idx
ON reports_teacher (role_id);
"""

# 4) زرع أدوار قياسية (لن تتكرر بفضل ON CONFLICT)
SEED_ROLES_SQL = r"""
INSERT INTO reports_role (slug, name, is_staff_by_default, is_active)
VALUES
    ('manager', 'المدير', TRUE, TRUE),
    ('activity_officer', 'مسؤول النشاط', TRUE, TRUE),
    ('volunteer_officer', 'مسؤول التطوع', TRUE, TRUE),
    ('affairs_officer', 'مسؤول الشؤون الطلابية', TRUE, TRUE),
    ('admin_officer', 'مسؤول الشؤون الإدارية', TRUE, TRUE),
    ('teacher', 'المعلم', FALSE, TRUE)
ON CONFLICT (slug) DO UPDATE
SET name = EXCLUDED.name,
    is_staff_by_default = EXCLUDED.is_staff_by_default,
    is_active = EXCLUDED.is_active;
"""

# 5) تعبئة role_id من العمود القديم النصي t.role **فقط إذا كان العمود موجودًا**
BACKFILL_FROM_LEGACY_TEXT_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'reports_teacher'
          AND column_name = 'role'
    ) THEN
        UPDATE reports_teacher t
        SET role_id = r.id
        FROM reports_role r
        WHERE t.role_id IS NULL
          AND t.role IS NOT NULL
          AND t.role <> ''
          AND r.slug = t.role;
    END IF;
END
$$;
"""

# 6) (اختياري) مزامنة is_staff مع الدور الافتراضي — آمنة على القواعد الجديدة
SYNC_IS_STAFF_SQL = r"""
UPDATE reports_teacher t
SET is_staff = r.is_staff_by_default
FROM reports_role r
WHERE t.role_id = r.id
  AND (t.is_staff IS DISTINCT FROM r.is_staff_by_default);
"""

class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0001_initial"),
    ]
    operations = [
        migrations.RunSQL(ADD_ROLE_ID_COL_SQL, reverse_sql=""),
        migrations.RunSQL(ADD_ROLE_FK_SQL, reverse_sql=""),
        migrations.RunSQL(ADD_ROLE_ID_INDEX_SQL, reverse_sql=""),
        migrations.RunSQL(SEED_ROLES_SQL, reverse_sql=""),
        migrations.RunSQL(BACKFILL_FROM_LEGACY_TEXT_SQL, reverse_sql=""),
        migrations.RunSQL(SYNC_IS_STAFF_SQL, reverse_sql=""),
    ]
