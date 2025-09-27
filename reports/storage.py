# reports/storage.py
# -*- coding: utf-8 -*-
from cloudinary_storage.storage import RawMediaCloudinaryStorage

class PublicRawMediaStorage(RawMediaCloudinaryStorage):
    """
    تخزين Cloudinary للملفات العامة كـ RAW (PDF/DOCX/ZIP..).
    - يرفع الملفات تحت resource_type="raw"
    - الوصول عام (type=upload)
    """
    # لا حاجة لإعادة تعريف __init__، لأن RawMediaCloudinaryStorage
    # بالفعل يضبط resource_type="raw" و type="upload".
    ...
