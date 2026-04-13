from pathlib import Path

from app.thirdparty.social_auto_upload.conf import BASE_DIR

Path(BASE_DIR / "cookies" / "xiaohongshu_uploader").mkdir(exist_ok=True)