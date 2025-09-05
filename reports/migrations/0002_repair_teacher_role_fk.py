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

# 3.1) تأكيد وجود فهرس/قيّد فريد على code لاستخدام ON CONFLICT
ENSURE_ROLE_CODE_UNIQUE_SQL = r"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'reports_role'::regclass
          AND conname = 'reports_role_code_key'
    ) THEN
        ALTER TABLE reports_role
        ADD CONSTRAINT reports_role_code_key UNIQUE (code);
    END IF;
END $$;
"""

# 4) ضبط DEFAULT للأعمدة المنطقية (تجنّب NULL مستقبلًا)
ENSURE_DEFAULTS_SQL = r"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='reports_role' AND column_name='can_view_all_reports') THEN
        ALTER TABLE reports_role ALTER COLUMN can_view_all_reports SET DEFAULT FALSE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='reports_role' AND column_name='can_manage_teachers') THEN
        ALTER TABLE reports_role ALTER COLUMN can_manage_teachers SET DEFAULT FALSE;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='reports_role' AND column_name='can_manage_requests') THEN
        ALTER TABLE reports_role ALTER COLUMN can_manage_requests SET DEFAULT FALSE;
    END IF;
END $$;
"""

# 5) معالجة أي NULLs حالية (لو وُجدت)
BACKFILL_NULLS_SQL = r"""
UPDATE reports_role SET can_view_all_reports = FALSE WHERE can_view_all_reports IS NULL;
UPDATE reports_role SET can_manage_teachers = FALSE WHERE can_manage_teachers IS NULL;
UPDATE reports_role SET can_manage_requests = FALSE WHERE can_manage_requests IS NULL;
"""

# 6) زرع/تحديث الأدوار — لاحظ أننا نُمرّر كل الأعمدة بما فيها can_view_all_reports
SEED_ROLES_SQL = r"""
INSERT INTO reports_role (code, name, can_manage_teachers, can_view_all_reports, can_manage_requests)
VALUES
('manager',           'المدير',        TRUE,  TRUE,  TRUE),
('admin_officer',     'ضابط إداري',    TRUE,  TRUE,  TRUE),
('activity_officer',  'ضابط النشاط',   FALSE, FALSE, TRUE),
('volunteer_officer', 'ضابط التطوع',   FALSE, FALSE, TRUE),
('affairs_officer',   'ضابط الشؤون',   FALSE, FALSE, TRUE),
('teacher',           'معلم/معلمة',    FALSE, FALSE, FALSE)
ON CONFLICT (code) DO UPDATE SET
    name                 = EXCLUDED.name,
    can_manage_teachers  = EXCLUDED.can_manage_teachers,
    can_view_all_reports = EXCLUDED.can_view_all_reports,
    can_manage_requests  = EXCLUDED.can_manage_requests;
"""

class Migration(migrations.Migration):
    dependencies = [
        ('reports', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(ADD_ROLE_ID_COL_SQL),
        migrations.RunSQL(ADD_ROLE_FK_SQL),
        migrations.RunSQL(ADD_ROLE_ID_INDEX_SQL),
        migrations.RunSQL(ENSURE_ROLE_CODE_UNIQUE_SQL),
        migrations.RunSQL(ENSURE_DEFAULTS_SQL),
        migrations.RunSQL(BACKFILL_NULLS_SQL),
        migrations.RunSQL(SEED_ROLES_SQL),
    ]
