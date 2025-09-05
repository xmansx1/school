# reports/migrations/0002_repair_teacher_role_fk.py
from django.db import migrations

# ملاحظات:
# - هذه هجرة "ترميم" تضيف فعليًا العمود role_id بقاعدة البيانات
#   وتملأه بناءً على العمود النصي القديم reports_teacher.role (إن وُجد)
# - لا نعدل State لدجانغو لأن الكود/الهجرات الحالية تظن أن FK موجود أصلًا.
# - تعمل بأمان حتى لو كانت بعض الجداول/القيود موجودة مسبقًا (IF NOT EXISTS).

CREATE_ROLE_TABLE_SQL = r"""
CREATE TABLE IF NOT EXISTS reports_role (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(120) NOT NULL,
    is_staff_by_default BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);
"""

ADD_ROLE_ID_COLUMN_SQL = r"""
ALTER TABLE reports_teacher
ADD COLUMN IF NOT EXISTS role_id INTEGER NULL;
"""

# اسم القيد سنجعله معروفًا لتجنّب التكرار
ADD_FK_CONSTRAINT_SQL = r"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'reports_teacher_role_id_fk'
    ) THEN
        ALTER TABLE reports_teacher
        ADD CONSTRAINT reports_teacher_role_id_fk
        FOREIGN KEY (role_id) REFERENCES reports_role (id)
        ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;
    END IF;
END
$$;
"""

# إدخال أدوار مفقودة حسب القيم النصية القديمة في reports_teacher.role
SEED_ROLES_FROM_TEACHER_SQL = r"""
INSERT INTO reports_role (slug, name)
SELECT DISTINCT t.role, t.role
FROM reports_teacher t
LEFT JOIN reports_role r ON r.slug = t.role
WHERE t.role IS NOT NULL AND r.id IS NULL;
"""

# تعبئة role_id من slug القديم
FILL_ROLE_ID_SQL = r"""
UPDATE reports_teacher t
SET role_id = r.id
FROM reports_role r
WHERE t.role IS NOT NULL AND r.slug = t.role
  AND (t.role_id IS NULL OR t.role_id <> r.id);
"""

# مواءمة is_staff مع الإعداد الافتراضي للدور (اختياري لكنه مفيد)
SYNC_IS_STAFF_SQL = r"""
UPDATE reports_teacher t
SET is_staff = r.is_staff_by_default
FROM reports_role r
WHERE t.role_id = r.id
  AND t.is_staff IS DISTINCT FROM r.is_staff_by_default;
"""

class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0001_initial"),  # نعتمد على 0001 كما هي في الإنتاج
    ]

    # نستخدم RunSQL فقط (بدون state_operations) لأن State عند دجانغو يعتبر FK موجود أصلاً
    operations = [
        migrations.RunSQL(CREATE_ROLE_TABLE_SQL, reverse_sql=""),
        migrations.RunSQL(ADD_ROLE_ID_COLUMN_SQL, reverse_sql=""),
        migrations.RunSQL(SEED_ROLES_FROM_TEACHER_SQL, reverse_sql=""),
        migrations.RunSQL(ADD_FK_CONSTRAINT_SQL, reverse_sql=""),
        migrations.RunSQL(FILL_ROLE_ID_SQL, reverse_sql=""),
        migrations.RunSQL(SYNC_IS_STAFF_SQL, reverse_sql=""),
    ]
