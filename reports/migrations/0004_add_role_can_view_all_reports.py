from django.db import migrations

SQL = """
ALTER TABLE reports_role
ADD COLUMN IF NOT EXISTS can_view_all_reports BOOLEAN NOT NULL DEFAULT FALSE;
"""

class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0003_repair_ticket_department_fk"),
    ]
    operations = [
        migrations.RunSQL(SQL, reverse_sql="ALTER TABLE reports_role DROP COLUMN IF EXISTS can_view_all_reports;"),
    ]
