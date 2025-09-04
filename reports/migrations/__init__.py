def seed(apps, schema_editor):
    try:
        Report = apps.get_model("reports", "Report")
        ReportType = apps.get_model("reports", "ReportType")
    except Exception:
        return

    codes = (
        Report.objects.exclude(category__isnull=True)
        .exclude(category__exact="")
        .values_list("category", flat=True)
        .distinct()
    )
    for code in codes:
        code_s = (code or "").strip()
        if not code_s:
            continue
        ReportType.objects.get_or_create(
            code=code_s,
            defaults={"name": code_s, "is_active": True, "order": 0},
        )

def unseed(apps, schema_editor):
    # لا نحذف شيئًا حفاظًا على البيانات
    pass
