import os

from telethon import TelegramClient, errors
from telethon.tl.types import DocumentAttributeVideo


class TelegramService:
    """
    Facade sobre Telethon. Solo conoce Telegram — NO procesa videos.
    """

    def __init__(self, session_path: str, api_id: int, api_hash: str):
        self.client = TelegramClient(session_path, int(api_id), api_hash)
        self.phone: str | None = None

    # ─── Autenticación ───
    async def connect_and_check(self) -> bool:
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str):
        self.phone = phone
        return await self.client.send_code_request(phone)

    async def sign_in(self, code: str, password: str | None = None):
        try:
            if password:
                await self.client.sign_in(password=password)
            else:
                await self.client.sign_in(self.phone, code)
            return True
        except errors.SessionPasswordNeededError:
            return "NEED_PASSWORD"

    # ─── Datos ───
    async def get_remote_captions(self, channel_id: int) -> set[str]:
        captions: set[str] = set()
        try:
            entity = await self.client.get_entity(int(channel_id))
            async for message in self.client.iter_messages(entity, limit=None):
                if message.message:
                    captions.add(message.message.strip())
        except Exception as e:
            print(f"[Telegram] captions error: {e}")
        return captions

    # ─── Subida ───
    async def send_video(
        self,
        channel_id: int,
        file_path: str,
        caption: str,
        duration: int,
        width: int,
        height: int,
        thumb_path: str | None,
        progress_callback,
    ):
        attributes = [
            DocumentAttributeVideo(
                duration=duration,
                w=width,
                h=height,
                supports_streaming=True,
            )
        ]
        entity = await self.client.get_entity(int(channel_id))
        return await self.client.send_file(
            entity,
            file_path,
            caption=caption,
            attributes=attributes,
            thumb=thumb_path,
            mime_type="video/mp4",
            force_document=False,
            supports_streaming=True,
            progress_callback=progress_callback,
        )

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()