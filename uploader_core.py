import os
import re
import sys
import struct
import subprocess
import tempfile
from telethon import TelegramClient, errors
from telethon.tl.types import DocumentAttributeVideo

import imageio_ffmpeg
from PIL import Image

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()


def _silent_subprocess_kwargs():
    """Argumentos para que no aparezca consola en Windows al ejecutar ffmpeg."""
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


class TelegramService:
    def __init__(self, session_path, api_id, api_hash):
        self.client = TelegramClient(session_path, int(api_id), api_hash)
        self.phone = None

    async def connect_and_check(self):
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone):
        self.phone = phone
        return await self.client.send_code_request(phone)

    async def sign_in(self, code, password=None):
        try:
            if password:
                await self.client.sign_in(password=password)
            else:
                await self.client.sign_in(self.phone, code)
            return True
        except errors.SessionPasswordNeededError:
            return "NEED_PASSWORD"

    async def get_remote_captions(self, channel_id):
        captions = set()
        try:
            entity = await self.client.get_entity(int(channel_id))
            async for message in self.client.iter_messages(entity, limit=None):
                if message.message:
                    captions.add(message.message.strip())
        except Exception as e:
            print("Error captions:", e)
        return captions

    # ═════════════════════════════════════════════════════════════
    # ANALISIS RAPIDO DEL VIDEO (codec + faststart + metadata)
    # ═════════════════════════════════════════════════════════════
    def has_faststart(self, file_path):
        """Lee atomos MP4 sin cargar el archivo. Retorna True si moov < mdat."""
        try:
            file_size = os.path.getsize(file_path)
            with open(file_path, "rb") as f:
                offset = 0
                moov_pos = None
                mdat_pos = None
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
                return moov_pos is not None and mdat_pos is not None and moov_pos < mdat_pos
        except Exception as e:
            print("faststart check error:", e)
            return False

    def analyze_video(self, file_path):
        """
        Una sola llamada a ffmpeg para obtener TODO:
        codec video/audio, pix_fmt, ancho, alto, duracion.
        Es muy rapido (~200ms) porque ffmpeg solo lee el header.
        """
        info = {
            "duration": 1,
            "width": 1280,
            "height": 720,
            "vcodec": "",
            "acodec": "",
            "pix_fmt": "",
            "compatible": False,
        }
        try:
            cmd = [FFMPEG_PATH, "-i", file_path]
            result = subprocess.run(cmd, **_silent_subprocess_kwargs())
            output = result.stderr.decode("utf-8", errors="ignore")

            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", output)
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                info["duration"] = max(1, int(h * 3600 + mi * 60 + s))

            m = re.search(r"Stream.*?Video:\s*([a-zA-Z0-9_]+).*?(\d{2,5})x(\d{2,5})", output)
            if m:
                info["vcodec"] = m.group(1).lower()
                info["width"] = int(m.group(2))
                info["height"] = int(m.group(3))

            m = re.search(r"Stream.*?Audio:\s*([a-zA-Z0-9_]+)", output)
            if m:
                info["acodec"] = m.group(1).lower()

            if "yuv420p" in output:
                info["pix_fmt"] = "yuv420p"

            info["compatible"] = (
                info["vcodec"] == "h264"
                and info["acodec"] == "aac"
                and info["pix_fmt"] == "yuv420p"
            )
        except Exception as e:
            print("analyze error:", e)

        # Fallback metadata con hachoir (por si ffmpeg fallara en el .exe)
        if info["duration"] <= 1 or info["width"] <= 1:
            try:
                from hachoir.parser import createParser
                from hachoir.metadata import extractMetadata
                parser = createParser(file_path)
                if parser:
                    with parser:
                        meta = extractMetadata(parser)
                    if meta:
                        if meta.has("duration"):
                            info["duration"] = max(1, int(meta.get("duration").total_seconds()))
                        if meta.has("width"):
                            info["width"] = int(meta.get("width"))
                        if meta.has("height"):
                            info["height"] = int(meta.get("height"))
            except Exception as e:
                print("hachoir fallback error:", e)

        return info

    # ═════════════════════════════════════════════════════════════
    # REMUX RAPIDO (solo mueve moov al inicio, sin re-encode)
    # ═════════════════════════════════════════════════════════════
    def remux_faststart(self, input_path):
        """Copia streams sin re-codificar y pone moov al inicio. Muy rapido."""
        # Generar la ruta en la carpeta temporal de Windows/OS
        filename = os.path.basename(input_path).rsplit(".", 1)[0] + "_fs.mp4"
        output_path = os.path.join(tempfile.gettempdir(), filename)
        
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path,
            "-c", "copy", "-movflags", "+faststart",
            output_path,
        ]
        process = subprocess.run(cmd, **_silent_subprocess_kwargs())
        if process.returncode != 0:
            print("Remux error:", process.stderr.decode("utf-8", errors="ignore")[-300:])
            return None
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 10_000:
            return None
        return output_path

    # ═════════════════════════════════════════════════════════════
    # RE-ENCODE (solo cuando el codec NO es compatible)
    # ═════════════════════════════════════════════════════════════
    def encode_video(self, input_path):
        # Generar la ruta en la carpeta temporal de Windows/OS
        filename = os.path.basename(input_path).rsplit(".", 1)[0] + "_encoded.mp4"
        output_path = os.path.join(tempfile.gettempdir(), filename)
        
        cmd = [
            FFMPEG_PATH, "-y", "-i", input_path,
            "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-movflags", "+faststart",
            output_path,
        ]
        process = subprocess.run(cmd, **_silent_subprocess_kwargs())
        if process.returncode != 0:
            print("FFmpeg error:", process.stderr.decode("utf-8", errors="ignore")[-300:])
            return None
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 10_000:
            return None
        return output_path

    # ═════════════════════════════════════════════════════════════
    # THUMBNAIL (max 320px, JPEG <200KB)
    # ═════════════════════════════════════════════════════════════
    def generate_thumbnail(self, video_path, duration):
        # Usar la carpeta temporal
        raw_thumb = os.path.join(tempfile.gettempdir(), "thumb_raw.jpg")
        final_thumb = os.path.join(tempfile.gettempdir(), "thumb.jpg")
        seek_time = max(0.1, duration * 0.1)

        cmd = [
            FFMPEG_PATH,
            "-y",
            "-ss", str(seek_time),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            raw_thumb,
        ]
        try:
            subprocess.run(cmd, **_silent_subprocess_kwargs())
            if not os.path.exists(raw_thumb):
                return None
            with Image.open(raw_thumb) as img:
                img = img.convert("RGB")
                img.thumbnail((320, 320), Image.LANCZOS)
                img.save(final_thumb, "JPEG", quality=85, optimize=True)
            os.remove(raw_thumb)
            return final_thumb
        except Exception as e:
            print("Thumbnail error:", e)
            for p in (raw_thumb, final_thumb):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except:
                        pass
            return None

    # ═════════════════════════════════════════════════════════════
    # UPLOAD INTELIGENTE
    # ═════════════════════════════════════════════════════════════
    async def upload_file(self, channel_id, file_path, progress_callback):
        info = self.analyze_video(file_path)
        duration = info["duration"]
        width = info["width"]
        height = info["height"]

        temp_file = None
        final_path = file_path

        if info["compatible"]:
            if self.has_faststart(file_path):
                print("Video ya optimizado, subida directa")
                final_path = file_path
            else:
                print("Remuxing faststart...")
                temp_file = self.remux_faststart(file_path)
                final_path = temp_file if temp_file else file_path
        else:
            print(f"Re-encodeando (codec no compatible: {info['vcodec']}/{info['acodec']})")
            temp_file = self.encode_video(file_path)
            final_path = temp_file if temp_file else file_path

        thumb_path = self.generate_thumbnail(final_path, duration)

        attributes = [
            DocumentAttributeVideo(
                duration=duration,
                w=width,
                h=height,
                supports_streaming=True,
            )
        ]

        try:
            entity = await self.client.get_entity(int(channel_id))
            return await self.client.send_file(
                entity,
                final_path,
                caption=os.path.splitext(os.path.basename(file_path))[0],
                attributes=attributes,
                thumb=thumb_path,
                mime_type="video/mp4",
                force_document=False,
                supports_streaming=True,
                progress_callback=progress_callback,
            )
        finally:
            if thumb_path and os.path.exists(thumb_path):
                try:
                    os.remove(thumb_path)
                except:
                    pass
            if temp_file and os.path.exists(temp_file) and temp_file != file_path:
                try:
                    os.remove(temp_file)
                except:
                    pass

    async def disconnect(self):
        if self.client:
            await self.client.disconnect()