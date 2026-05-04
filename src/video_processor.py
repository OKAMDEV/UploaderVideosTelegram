import os
import subprocess
import tempfile
from abc import ABC, abstractmethod

from .video_analyzer import (
    FFMPEG_PATH,
    VideoInfo,
    silent_subprocess_kwargs,
)


class ProcessRegistry:
    """Permite al GUI matar el proceso ffmpeg en curso (Observer sencillo)."""

    def __init__(self):
        self._current: subprocess.Popen | None = None

    def set(self, proc: subprocess.Popen) -> None:
        self._current = proc

    def terminate(self) -> None:
        proc = self._current
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


# ─────────── Strategy Pattern ───────────
class ProcessingStrategy(ABC):
    """Interfaz común para todas las estrategias de procesamiento."""

    def __init__(self, registry: ProcessRegistry):
        self._registry = registry

    @abstractmethod
    def process(self, input_path: str) -> tuple[str, bool]:
        """
        Retorna (output_path, is_temp_file).
        is_temp_file indica si el archivo debe eliminarse tras subirlo.
        """


class DirectPassthrough(ProcessingStrategy):
    """Video ya óptimo: no toca el archivo."""

    def process(self, input_path: str) -> tuple[str, bool]:
        print("[Strategy] Direct passthrough (0 procesamiento)")
        return input_path, False


class FastStartRemux(ProcessingStrategy):
    """Copia streams y mueve el átomo moov al inicio (muy rápido)."""

    def process(self, input_path: str) -> tuple[str, bool]:
        print("[Strategy] FastStart remux")
        output = os.path.join(
            tempfile.gettempdir(),
            os.path.basename(input_path).rsplit(".", 1)[0] + "_fs.mp4",
        )
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path,
            "-c", "copy", "-movflags", "+faststart",
            output,
        ]
        proc = subprocess.Popen(cmd, **silent_subprocess_kwargs())
        self._registry.set(proc)
        proc.communicate()
        if proc.returncode != 0 or not os.path.exists(output):
            return input_path, False
        return output, True


class FullReEncode(ProcessingStrategy):
    """Último recurso: re-encodea todo."""

    def process(self, input_path: str) -> tuple[str, bool]:
        print("[Strategy] Full re-encode")
        output = os.path.join(
            tempfile.gettempdir(),
            os.path.basename(input_path).rsplit(".", 1)[0] + "_encoded.mp4",
        )
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path,
            "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart",
            output,
        ]
        proc = subprocess.Popen(cmd, **silent_subprocess_kwargs())
        self._registry.set(proc)
        proc.communicate()
        if proc.returncode != 0 or not os.path.exists(output):
            return input_path, False
        return output, True


# ─────────── Factory ───────────
class ProcessingStrategyFactory:
    """Elige la estrategia óptima según el análisis del video."""

    @staticmethod
    def for_video(info: VideoInfo, registry: ProcessRegistry) -> ProcessingStrategy:
        if info.is_telegram_compatible and info.has_faststart:
            return DirectPassthrough(registry)
        if info.is_telegram_compatible:
            return FastStartRemux(registry)
        return FullReEncode(registry)