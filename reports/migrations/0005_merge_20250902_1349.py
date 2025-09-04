# reports/migrations/0005_merge_20250902_1234.py
from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0002_alter_teacher_options_and_more"),
        ("reports", "0004_report_teacher_name_alter_report_category_and_more"),
    ]

    operations = [
        # لا شيء: مجرّد فضّ تعارض المسار
    ]
