# reports/migrations/0003_repair_ticket_department_fk.py
from django.db import migrations

# ننشئ جدول الأقسام إن لم يوجد
CREATE_DEPARTMENT_TABLE_SQL = r"""
CREATE TABLE IF NOT EXISTS reports_department (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    slug VARCHAR(64) UNIQUE NOT NULL,
    role_label VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
"""

# بذور قياسية (تراعي NOT NULL للـ role_label)
SEED_KNOWN_DEPARTMENTS_SQL = r"""
INSERT INTO reports_department (slug, name, role_label, is_active)
VALUES
    ('activity_officer', 'قسم النشاط', 'مسؤول النشاط', TRUE),
    ('volunteer_officer', 'قسم التطوع', 'مسؤول التطوع', TRUE),
    ('affairs_officer',  'قسم شؤون الطلاب', 'مسؤول الشؤون الطلابية', TRUE),
    ('admin_officer',    'قسم الشؤون الإدارية', 'مسؤول الشؤون الإدارية', TRUE),
    ('manager',          'الإدارة', 'المدير', TRUE),
    ('teacher',          'المعلمين', 'المعلم', TRUE)
ON CONFLICT (slug) DO NOTHING;
"""

# عمود FK للتذاكر
ADD_TICKET_DEPT_ID_COLUMN_SQL = r"""
ALTER TABLE reports_ticket
ADD COLUMN IF NOT EXISTS department_id INTEGER NULL;
"""

# استيراد أقسام ظهرت كنصوص قديمة داخل التذاكر (مع role_label = name ليتوافق مع NOT NULL)
SEED_DEPTS_FROM_TICKETS_SQL = r"""
INSERT INTO reports_department (slug, name, role_label, is_active)
SELECT DISTINCT t.department, t.department, t.department, TRUE
FROM reports_ticket t
LEFT JOIN reports_department d ON d.slug = t.department
WHERE t.department IS NOT NULL AND t.department <> '' AND d.id IS NULL;
"""

# تعبئة FK
FILL_TICKET_DEPT_ID_SQL = r"""
UPDATE reports_ticket t
SET department_id = d.id
FROM reports_department d
WHERE t.department IS NOT NULL AND t.department <> '' AND d.slug = t.department
  AND (t.department_id IS NULL OR t.department_id <> d.id);
"""

# فهارس
CREATE_TICKET_DEPT_INDEX_SQL = r"""
CREATE INDEX IF NOT EXISTS reports_ticket_department_id_idx
    ON reports_ticket (department_id);
"""
CREATE_TICKET_DEPT_STATUS_CREATED_IDX_SQL = r"""
CREATE INDEX IF NOT EXISTS reports_ticket_dept_status_created_idx
    ON reports_ticket (department_id, status, created_at);
"""

# FK NOT VALID أولاً… ثم VALIDATE في أمر مستقل لتجنّب مشاكل الـ triggers
ADD_TICKET_DEPT_FK_NOT_VALID_SQL = r"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'reports_ticket_department_id_fk'
    ) THEN
        ALTER TABLE reports_ticket
        ADD CONSTRAINT reports_ticket_department_id_fk
        FOREIGN KEY (department_id) REFERENCES reports_department (id)
        ON DELETE SET NULL NOT VALID;
    END IF;
END
$$;
"""

VALIDATE_TICKET_DEPT_FK_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'reports_ticket_department_id_fk' AND NOT convalidated
    ) THEN
        ALTER TABLE reports_ticket VALIDATE CONSTRAINT reports_ticket_department_id_fk;
    END IF;
END
$$;
"""

class Migration(migrations.Migration):
    # مهم جداً لتفادي "pending trigger events" — نفصل المعاملات
    atomic = False

    dependencies = [
        ("reports", "0002_repair_teacher_role_fk"),
    ]

    operations = [
        migrations.RunSQL(CREATE_DEPARTMENT_TABLE_SQL,  reverse_sql=""),
        migrations.RunSQL(SEED_KNOWN_DEPARTMENTS_SQL,   reverse_sql=""),
        migrations.RunSQL(ADD_TICKET_DEPT_ID_COLUMN_SQL, reverse_sql=""),
        migrations.RunSQL(SEED_DEPTS_FROM_TICKETS_SQL,  reverse_sql=""),
        migrations.RunSQL(FILL_TICKET_DEPT_ID_SQL,      reverse_sql=""),
        migrations.RunSQL(CREATE_TICKET_DEPT_INDEX_SQL, reverse_sql=""),
        migrations.RunSQL(CREATE_TICKET_DEPT_STATUS_CREATED_IDX_SQL, reverse_sql=""),
        migrations.RunSQL(ADD_TICKET_DEPT_FK_NOT_VALID_SQL, reverse_sql=""),
        migrations.RunSQL(VALIDATE_TICKET_DEPT_FK_SQL,  reverse_sql=""),
    ]
