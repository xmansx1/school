# reports/migrations/0003_repair_ticket_department_fk.py
from django.db import migrations

# إنشاء جدول الأقسام إن لم يكن موجودًا (لن يعمل إن كان مُنشأ من 0001)
CREATE_DEPARTMENT_TABLE_SQL = r"""
CREATE TABLE IF NOT EXISTS reports_department (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    slug VARCHAR(64) UNIQUE NOT NULL,
    role_label VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
"""

# بذور أساسية للأقسام المعروفة
SEED_KNOWN_DEPARTMENTS_SQL = r"""
INSERT INTO reports_department (name, slug, role_label, is_active)
VALUES
('الإدارة',            'admin_officer',     'ضابط إداري',            TRUE),
('النشاط الطلابي',     'activity_officer',  'ضابط النشاط',           TRUE),
('التطوع',             'volunteer_officer', 'ضابط التطوع',           TRUE),
('الشؤون المدرسية',    'affairs_officer',   'ضابط الشؤون المدرسية',  TRUE),
('الإدارة العليا',     'manager',           'المدير',                TRUE)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    role_label = EXCLUDED.role_label,
    is_active = EXCLUDED.is_active;
"""

# إضافة عمود FK إن لم يوجد (سيكون موجودًا أصلًا من 0001، فلا ضرر)
ADD_TICKET_DEPT_ID_COLUMN_SQL = r"""
ALTER TABLE reports_ticket
ADD COLUMN IF NOT EXISTS department_id INTEGER NULL;
"""

# استيراد أقسام من عمود نصي قديم إن وُجد 'department'
SEED_DEPTS_FROM_TICKETS_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='reports_ticket' AND column_name='department'
    ) THEN
        INSERT INTO reports_department (name, slug, role_label, is_active)
        SELECT DISTINCT
            INITCAP(REPLACE(TRIM(t.department), '_', ' ')) AS name,
            LOWER(TRIM(t.department)) AS slug,
            INITCAP(REPLACE(TRIM(t.department), '_', ' ')) AS role_label,
            TRUE
        FROM reports_ticket t
        WHERE t.department IS NOT NULL AND t.department <> ''
        ON CONFLICT (slug) DO NOTHING;
    END IF;
END $$;
"""

# تعبئة department_id من العمود النصي القديم إن وُجد
FILL_TICKET_DEPT_ID_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='reports_ticket' AND column_name='department'
    ) THEN
        UPDATE reports_ticket AS t
        SET department_id = d.id
        FROM reports_department AS d
        WHERE LOWER(TRIM(t.department)) = d.slug
          AND (t.department_id IS NULL OR t.department_id = 0);
    END IF;
END $$;
"""

# فهارس مناسبة
CREATE_TICKET_DEPT_INDEX_SQL = r"""
CREATE INDEX IF NOT EXISTS reports_ticket_department_id_idx
ON reports_ticket (department_id);
"""

CREATE_TICKET_DEPT_STATUS_CREATED_IDX_SQL = r"""
CREATE INDEX IF NOT EXISTS reports_ticket_dept_status_created_idx
ON reports_ticket (department_id, status, created_at);
"""

# إضافة FK فقط في حالة وجود العمود النصي القديم (سيناريو ترحيل قديم)
ADD_TICKET_DEPT_FK_NOT_VALID_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='reports_ticket' AND column_name='department'
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'reports_ticket_department_id_fk'
        ) THEN
            ALTER TABLE reports_ticket
            ADD CONSTRAINT reports_ticket_department_id_fk
            FOREIGN KEY (department_id) REFERENCES reports_department (id)
            ON DELETE SET NULL
            DEFERRABLE INITIALLY DEFERRED
            NOT VALID;
        END IF;
    END IF;
END $$;
"""

# تفعيل/التحقق من الـ FK إن كان قد أضيف أعلاه
VALIDATE_TICKET_DEPT_FK_SQL = r"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'reports_ticket_department_id_fk'
    ) THEN
        ALTER TABLE reports_ticket VALIDATE CONSTRAINT reports_ticket_department_id_fk;
    END IF;
END $$;
"""

class Migration(migrations.Migration):
    dependencies = [
        ('reports', '0002_repair_teacher_role_fk'),
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
