import os
import subprocess
import tempfile

from PIL import Image

from .video_analyzer import FFMPEG_PATH, silent_subprocess_kwargs

class ThumbnailGenerator:
    """
    Genera un thumbnail JPEG optimizado para Telegram.
    Reglas: max 320px lado mayor, quality 85, <200KB.
    """

    MAX_SIZE = (320, 320)
    JPEG_QUALITY = 85

    def __init__(self, process_registry=None):
        # process_registry permite al GUI matar el proceso de ffmpeg
        self._process_registry = process_registry

    def generate(self, video_path: str, duration: int) -> str | None:
        tmp_dir = tempfile.gettempdir()
        raw = os.path.join(tmp_dir, "thumb_raw.jpg")
        final = os.path.join(tmp_dir, "thumb.jpg")
        seek_time = max(0.1, duration * 0.1)

        cmd = [
            FFMPEG_PATH,
            "-y",
            "-ss", str(seek_time),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            raw,
        ]

        try:
            proc = subprocess.Popen(cmd, **silent_subprocess_kwargs())
            if self._process_registry:
                self._process_registry.set(proc)
            proc.wait()

            if not os.path.exists(raw):
                return None

            with Image.open(raw) as img:
                img = img.convert("RGB")
                img.thumbnail(self.MAX_SIZE, Image.LANCZOS)
                img.save(final, "JPEG", quality=self.JPEG_QUALITY, optimize=True)
            os.remove(raw)
            return final
        except Exception as e:
            print(f"[Thumbnail] error: {e}")
            for p in (raw, final):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            return None