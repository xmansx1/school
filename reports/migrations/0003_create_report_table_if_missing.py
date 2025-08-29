# reports/migrations/0003_create_report_table_if_missing.py
from django.db import migrations

SQL = r"""
DO $$
BEGIN
IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'reports_report'
) THEN
    CREATE TABLE public.reports_report (
        id BIGSERIAL PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        report_date DATE NOT NULL,
        day_name VARCHAR(20),
        beneficiaries_count INTEGER CHECK (beneficiaries_count >= 0),
        idea TEXT,
        category VARCHAR(32) NOT NULL,
        image1 VARCHAR(100),
        image2 VARCHAR(100),
        image3 VARCHAR(100),
        image4 VARCHAR(100),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        teacher_id BIGINT NOT NULL
            REFERENCES public.reports_teacher(id)
            ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS reports_report_report_date_idx
        ON public.reports_report (report_date);
END IF;
END
$$;
"""

class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0002_alter_teacher_options_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql=SQL,
            reverse_sql="DROP TABLE IF EXISTS public.reports_report CASCADE;"
        ),
    ]
