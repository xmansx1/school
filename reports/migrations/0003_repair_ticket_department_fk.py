# reports/migrations/0003_repair_ticket_department_fk.py
from django.db import migrations

# هجرة "ترميم" لبيئة الإنتاج (PostgreSQL):
# - تنشئ جدول reports_department إن لم يكن موجودًا
# - تضيف العمود reports_ticket.department_id (FK) إن لم يكن موجودًا
# - تزرع أقسامًا قياسية + تستخرج القيم القديمة من reports_ticket.department (النصي) وتحوّلها لـ FK
# - تضيف قيد FK وفهارس
# ملاحظة: نستخدم RunSQL فقط، بدون state changes، لأن Django يظن أن البُنى موجودة أصلًا.

CREATE_DEPARTMENT_TABLE_SQL = r"""
CREATE TABLE IF NOT EXISTS reports_department (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    slug VARCHAR(64) UNIQUE NOT NULL,
    role_label VARCHAR(120),
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
"""

# Seed قياسي للأقسام المعروفة (لن يكرر بفضل ON CONFLICT)
SEED_KNOWN_DEPARTMENTS_SQL = r"""
INSERT INTO reports_department (slug, name, role_label, is_active)
VALUES
    ('activity_officer', 'قسم النشاط', 'مسؤول النشاط', TRUE),
    ('volunteer_officer', 'قسم التطوع', 'مسؤول التطوع', TRUE),
    ('affairs_officer',  'قسم شؤون الطلاب', 'مسؤول الشؤون الطلابية', TRUE),
    ('admin_officer',    'قسم الشؤون الإدارية', 'مسؤول الشؤون الإدارية', TRUE),
    ('manager',          'الإدارة', 'المدير', TRUE)
ON CONFLICT (slug) DO NOTHING;
"""

# إضافة العمود FK إلى جدول التذاكر
ADD_TICKET_DEPT_ID_COLUMN_SQL = r"""
ALTER TABLE reports_ticket
ADD COLUMN IF NOT EXISTS department_id INTEGER NULL;
"""

# قيد الـ FK باسم معروف، نتجنب التكرار بفحص pg_constraint
ADD_TICKET_DEPT_FK_SQL = r"""
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
"""

# استيراد الأقسام تلقائيًا من القيم النصية القديمة في reports_ticket.department (مع تعبئة role_label)
SEED_DEPTS_FROM_TICKETS_SQL = r"""
INSERT INTO reports_department (slug, name, role_label, is_active)
SELECT DISTINCT t.department, t.department, t.department, TRUE
FROM reports_ticket t
LEFT JOIN reports_department d ON d.slug = t.department
WHERE t.department IS NOT NULL AND t.department <> '' AND d.id IS NULL;
"""

# ضمان تعبئة role_label لأي صفوف قديمة بلا قيمة
FILL_DEPT_ROLE_LABEL_SQL = r"""
UPDATE reports_department
SET role_label = name
WHERE role_label IS NULL OR role_label = '';
"""

# تعبئة العمود department_id بناءً على slug القديم
FILL_TICKET_DEPT_ID_SQL = r"""
UPDATE reports_ticket t
SET department_id = d.id
FROM reports_department d
WHERE t.department IS NOT NULL AND t.department <> '' AND d.slug = t.department
  AND (t.department_id IS NULL OR t.department_id <> d.id);
"""

# فهارس مفيدة للأداء
CREATE_TICKET_DEPT_INDEX_SQL = r"""
CREATE INDEX IF NOT EXISTS reports_ticket_department_id_idx
    ON reports_ticket (department_id);
"""
CREATE_TICKET_DEPT_STATUS_CREATED_IDX_SQL = r"""
CREATE INDEX IF NOT EXISTS reports_ticket_dept_status_created_idx
    ON reports_ticket (department_id, status, created_at);
"""

class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0002_repair_teacher_role_fk"),  # نتبع الترميم السابق
    ]

    operations = [
        migrations.RunSQL(CREATE_DEPARTMENT_TABLE_SQL, reverse_sql=""),
        migrations.RunSQL(SEED_KNOWN_DEPARTMENTS_SQL, reverse_sql=""),
        migrations.RunSQL(ADD_TICKET_DEPT_ID_COLUMN_SQL, reverse_sql=""),
        migrations.RunSQL(ADD_TICKET_DEPT_FK_SQL, reverse_sql=""),
        migrations.RunSQL(SEED_DEPTS_FROM_TICKETS_SQL, reverse_sql=""),
        migrations.RunSQL(FILL_DEPT_ROLE_LABEL_SQL, reverse_sql=""),
        migrations.RunSQL(FILL_TICKET_DEPT_ID_SQL, reverse_sql=""),
        migrations.RunSQL(CREATE_TICKET_DEPT_INDEX_SQL, reverse_sql=""),
        migrations.RunSQL(CREATE_TICKET_DEPT_STATUS_CREATED_IDX_SQL, reverse_sql=""),
    ]
