import os
import unicodedata
from typing import Callable

from .telegram_service import TelegramService
from .thumbnail_generator import ThumbnailGenerator
from .video_analyzer import VideoAnalyzer
from .video_processor import ProcessingStrategyFactory, ProcessRegistry


class SyncService:
    """
    Orquesta el flujo de sincronización.
    Recibe sus dependencias por inyección (DIP).
    """

    def __init__(
        self,
        telegram: TelegramService,
        analyzer: VideoAnalyzer,
        thumbnail: ThumbnailGenerator,
        registry: ProcessRegistry,
        on_log: Callable[[str], None],
        on_file_progress: Callable[[float], None],
        on_global_progress: Callable[[float], None],
        stop_requested: Callable[[], bool],
    ):
        self.telegram = telegram
        self.analyzer = analyzer
        self.thumbnail = thumbnail
        self.registry = registry
        self.on_log = on_log
        self.on_file_progress = on_file_progress
        self.on_global_progress = on_global_progress
        self.stop_requested = stop_requested

    @staticmethod
    def _normalize(text: str) -> str:
        return (
            unicodedata.normalize("NFKD", text)
            .strip()
            .lower()
            .replace(".mp4", "")
            .replace("  ", " ")
        )

    async def find_pending(self, channel_id: int, folder: str) -> list[str]:
        remote = {self._normalize(c) for c in await self.telegram.get_remote_captions(channel_id)}
        locales = [f for f in os.listdir(folder) if f.lower().endswith(".mp4")]
        return [
            f for f in locales
            if self._normalize(os.path.splitext(f)[0]) not in remote
        ]

    async def upload_one(self, channel_id: int, file_path: str) -> None:
        info = self.analyzer.analyze(file_path)
        strategy = ProcessingStrategyFactory.for_video(info, self.registry)
        final_path, is_temp = strategy.process(file_path)

        if not os.path.exists(final_path):
            return

        thumb = self.thumbnail.generate(final_path, info.duration)

        def progress_cb(current, total):
            if self.stop_requested():
                raise RuntimeError("Cancelado por usuario")
            self.on_file_progress(current / total)

        try:
            await self.telegram.send_video(
                channel_id=channel_id,
                file_path=final_path,
                caption=os.path.splitext(os.path.basename(file_path))[0],
                duration=info.duration,
                width=info.width,
                height=info.height,
                thumb_path=thumb,
                progress_callback=progress_cb,
            )
        finally:
            self._cleanup(thumb)
            if is_temp and final_path != file_path:
                self._cleanup(final_path)

    async def run(self, channel_id: int, folder: str) -> None:
        pending = await self.find_pending(channel_id, folder)
        if not pending:
            self.on_log("No hay videos nuevos.")
            return

        total = len(pending)
        for i, filename in enumerate(pending, start=1):
            if self.stop_requested():
                self.on_log(">>> Subida abortada.")
                break
            self.on_log(f"Subiendo [{i}/{total}]: {filename}")
            await self.upload_one(channel_id, os.path.join(folder, filename))
            self.on_file_progress(0)
            self.on_global_progress(i / total)
            self.on_log(f"Completado: {filename}")

    @staticmethod
    def _cleanup(path: str | None) -> None:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass