from pathlib import Path

from app.thirdparty.social_auto_upload.conf import BASE_DIR

Path(BASE_DIR).mkdir(parents=True, exist_ok=True)
Path(BASE_DIR / "cookies").mkdir(parents=True, exist_ok=True)
