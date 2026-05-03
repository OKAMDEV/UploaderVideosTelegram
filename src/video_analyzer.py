import os
import re
import struct
import subprocess
from dataclasses import dataclass

import imageio_ffmpeg

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()


def silent_subprocess_kwargs() -> dict:
    """Evita que aparezca consola en Windows al ejecutar ffmpeg."""
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


@dataclass
class VideoInfo:
    """Metadata inmutable de un video."""
    duration: int = 1
    width: int = 1280
    height: int = 720
    vcodec: str = ""
    acodec: str = ""
    pix_fmt: str = ""
    has_faststart: bool = False

    @property
    def is_telegram_compatible(self) -> bool:
        """True si el video NO requiere re-encode."""
        return (
            self.vcodec == "h264"
            and self.acodec == "aac"
            and self.pix_fmt == "yuv420p"
        )


class VideoAnalyzer:
    """
    Analiza videos sin modificarlos. Responsabilidad única: leer metadata
    y detectar compatibilidad con Telegram.
    """

    def analyze(self, file_path: str) -> VideoInfo:
        info = self._probe_with_ffmpeg(file_path)

        # Fallback a hachoir si ffmpeg no retornó datos válidos
        if info.duration <= 1 or info.width <= 1:
            info = self._fallback_hachoir(file_path, info)

        # Detectar faststart directamente desde los átomos mp4
        info.has_faststart = self._check_faststart(file_path)
        return info

    # ─────────── privado ───────────
    def _probe_with_ffmpeg(self, file_path: str) -> VideoInfo:
        info = VideoInfo()
        try:
            result = subprocess.run(
                [FFMPEG_PATH, "-i", file_path],
                **silent_subprocess_kwargs(),
            )
            output = result.stderr.decode("utf-8", errors="ignore")

            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", output)
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                info.duration = max(1, int(h * 3600 + mi * 60 + s))

            m = re.search(
                r"Stream.*?Video:\s*([a-zA-Z0-9_]+).*?(\d{2,5})x(\d{2,5})",
                output,
            )
            if m:
                info.vcodec = m.group(1).lower()
                info.width = int(m.group(2))
                info.height = int(m.group(3))

            m = re.search(r"Stream.*?Audio:\s*([a-zA-Z0-9_]+)", output)
            if m:
                info.acodec = m.group(1).lower()

            if "yuv420p" in output:
                info.pix_fmt = "yuv420p"
        except Exception as e:
            print(f"[Analyzer] ffmpeg error: {e}")
        return info

    def _fallback_hachoir(self, file_path: str, info: VideoInfo) -> VideoInfo:
        try:
            from hachoir.parser import createParser
            from hachoir.metadata import extractMetadata

            parser = createParser(file_path)
            if not parser:
                return info
            with parser:
                meta = extractMetadata(parser)
            if not meta:
                return info
            if meta.has("duration"):
                info.duration = max(1, int(meta.get("duration").total_seconds()))
            if meta.has("width"):
                info.width = int(meta.get("width"))
            if meta.has("height"):
                info.height = int(meta.get("height"))
        except Exception as e:
            print(f"[Analyzer] hachoir fallback error: {e}")
        return info

    def _check_faststart(self, file_path: str) -> bool:
        """True si el átomo 'moov' aparece antes que 'mdat'."""
        try:
            file_size = os.path.getsize(file_path)
            with open(file_path, "rb") as f:
                offset = 0
                moov_pos = mdat_pos = None
                for _ in range(50):
                    if offset >= file_size:
                        break
                    f.seek(offset)
                    header = f.read(8)
                    if len(header) < 8:
                        break
                    size = struct.unpack(">I", header[:4])[0]
                    atom_type = header[4:8]
                    if size == 1:
                        ext = f.read(8)
                        if len(ext) < 8:
                            break
                        size = struct.unpack(">Q", ext)[0]
                    elif size == 0:
                        size = file_size - offset
                    if atom_type == b"moov":
                        moov_pos = offset
                    elif atom_type == b"mdat":
                        mdat_pos = offset
                    if moov_pos is not None and mdat_pos is not None:
                        break
                    if size < 8:
                        break
                    offset += size
                return (
                    moov_pos is not None
                    and mdat_pos is not None
                    and moov_pos < mdat_pos
                )
        except Exception as e:
            print(f"[Analyzer] faststart check error: {e}")
            return False