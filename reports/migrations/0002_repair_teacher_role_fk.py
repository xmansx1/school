# reports/migrations/0002_repair_teacher_role_fk.py
from django.db import migrations

# نضبط DEFAULT ونُزيل أي NULLs على أعمدة Role المنطقية
ENSURE_DEFAULTS_SQL = r"""
ALTER TABLE reports_role ALTER COLUMN is_staff_by_default   SET DEFAULT FALSE;
ALTER TABLE reports_role ALTER COLUMN can_view_all_reports  SET DEFAULT FALSE;
ALTER TABLE reports_role ALTER COLUMN is_active             SET DEFAULT TRUE;

UPDATE reports_role SET is_staff_by_default = FALSE  WHERE is_staff_by_default IS NULL;
UPDATE reports_role SET can_view_all_reports = FALSE WHERE can_view_all_reports IS NULL;
UPDATE reports_role SET is_active = TRUE            WHERE is_active IS NULL;
"""

# زرع/تحديث الأدوار باستخدام slug (وليس code)
SEED_ROLES_SQL = r"""
INSERT INTO reports_role (slug, name, is_staff_by_default, can_view_all_reports, is_active)
VALUES
('manager',           'المدير',                TRUE,  TRUE,  TRUE),
('admin_officer',     'ضابط إداري',            TRUE,  TRUE,  TRUE),
('activity_officer',  'ضابط النشاط',           TRUE,  FALSE, TRUE),
('volunteer_officer', 'ضابط التطوع',           TRUE,  FALSE, TRUE),
('affairs_officer',   'ضابط الشؤون المدرسية',  TRUE,  FALSE, TRUE),
('teacher',           'معلم/معلمة',            FALSE, FALSE, TRUE)
ON CONFLICT (slug) DO UPDATE SET
    name                 = EXCLUDED.name,
    is_staff_by_default  = EXCLUDED.is_staff_by_default,
    can_view_all_reports = EXCLUDED.can_view_all_reports,
    is_active            = EXCLUDED.is_active;
"""

class Migration(migrations.Migration):
    dependencies = [
        ('reports', '0001_initial'),
    ]
    operations = [
        migrations.RunSQL(ENSURE_DEFAULTS_SQL),
        migrations.RunSQL(SEED_ROLES_SQL),
    ]
