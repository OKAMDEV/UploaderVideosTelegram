import asyncio
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk

from .paths import resource_path
from .sync_service import SyncService
from .telegram_service import TelegramService
from .thumbnail_generator import ThumbnailGenerator
from .video_analyzer import VideoAnalyzer
from .video_processor import ProcessRegistry


class LoginDialog(ctk.CTkToplevel):
    def __init__(self, master, title, prompt):
        super().__init__(master)
        self.title(title)
        self.geometry("300x180")
        self.resizable(False, False)
        self.result = None

        ctk.CTkLabel(self, text=prompt, wraplength=250).pack(pady=10)
        self.entry = ctk.CTkEntry(self)
        self.entry.pack(pady=10)
        self.entry.bind("<Return>", lambda e: self._on_ok())
        ctk.CTkButton(self, text="Aceptar", command=self._on_ok).pack(pady=10)
        self.grab_set()

    def _on_ok(self):
        self.result = self.entry.get()
        self.destroy()


class AppGui(ctk.CTk):
    """Solo responsabilidad: UI. Delega todo el trabajo a SyncService."""

    def __init__(self, config_manager):
        super().__init__()
        self.mgr = config_manager
        self.config = self.mgr.load()

        self.title("Uploader Videos Telegram")
        self.geometry("650x650")
        self.resizable(False, False)

        self.stop_requested = False
        self.telegram: TelegramService | None = None
        self.registry = ProcessRegistry()

        self._load_icon()
        self._build_layout()
        self._validate_fields()

    # ─────────── UI Setup ───────────
    def _load_icon(self):
        """
        Configura el ícono de la ventana (barra de título + barra de tareas en Windows).
        Prioriza .ico sobre .png porque Windows lo soporta nativamente.
        """
        # Windows: forzar que la app tenga su propio ID (sino usa el de Python)
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "telegram.uploader.pro.v1"
                )
            except Exception as e:
                print(f"[GUI] no se pudo setear AppUserModelID: {e}")

        ico_path = resource_path(os.path.join("assets", "logo.ico"))
        png_path = resource_path(os.path.join("assets", "logo.png"))

        # 1) .ico es lo mejor en Windows (barra de título + taskbar)
        if os.path.exists(ico_path):
            try:
                self.iconbitmap(default=ico_path)
                return
            except Exception as e:
                print(f"[GUI] iconbitmap falló: {e}")

        # 2) Fallback a .png con iconphoto (multiplataforma)
        if os.path.exists(png_path):
            try:
                img = Image.open(png_path)
                self.icon_photo = ImageTk.PhotoImage(img)
                # True = también aplica a toplevels (diálogos de login, etc.)
                self.wm_iconphoto(True, self.icon_photo)
            except Exception as e:
                print(f"[GUI] error cargando logo.png: {e}")
        else:
            print(f"[GUI] no se encontró logo en: {ico_path} ni {png_path}")

    def _build_layout(self):
        ctk.CTkLabel(self, text="Configuración", font=("Roboto", 22)).pack(pady=20)

        self.var_api_id = ctk.StringVar(value=str(self.config.get("api_id", "")))
        self.var_api_hash = ctk.StringVar(value=self.config.get("api_hash", ""))
        self.var_channel_id = ctk.StringVar(value=str(self.config.get("channel_id", "")))
        self.var_folder_path = ctk.StringVar(value=self.config.get("folder_path", ""))

        for var in (self.var_api_id, self.var_api_hash, self.var_channel_id, self.var_folder_path):
            var.trace_add("write", self._validate_fields)

        self._create_field("API ID", self.var_api_id)
        self._create_field("API Hash", self.var_api_hash)
        self._create_field("Channel ID", self.var_channel_id)
        self._create_field("Carpeta Videos", self.var_folder_path, has_button=True)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=20)

        self.btn_start = ctk.CTkButton(btn_frame, text="Sincronizar y Subir", command=self._run_process)
        self.btn_start.pack(side="left", padx=10)

        self.btn_stop = ctk.CTkButton(
            btn_frame, text="Detener", command=self._request_stop,
            fg_color="#D32F2F", hover_color="#B71C1C", state="disabled",
        )
        self.btn_stop.pack(side="left", padx=10)

        self.log_view = ctk.CTkTextbox(self)
        self.log_view.pack(padx=20, pady=10, fill="both", expand=True)

        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.pack(fill="x", padx=20, pady=5)
        self.progress_bar.set(0)

        self.global_progress = ctk.CTkProgressBar(self)
        self.global_progress.pack(fill="x", padx=20, pady=(0, 10))
        self.global_progress.set(0)

    def _create_field(self, label, variable, has_button=False):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=40, pady=5)
        ctk.CTkLabel(frame, text=label, width=120, anchor="w").pack(side="left")

        if has_button:
            container = ctk.CTkFrame(frame, fg_color="transparent")
            container.pack(side="right", fill="x", expand=True)
            ctk.CTkEntry(container, textvariable=variable).pack(side="left", fill="x", expand=True, padx=(0, 5))
            ctk.CTkButton(container, text="...", width=40, command=self._browse_folder).pack(side="right")
        else:
            ctk.CTkEntry(frame, textvariable=variable).pack(side="right", fill="x", expand=True)

    def _browse_folder(self):
        selected = filedialog.askdirectory()
        if selected:
            self.var_folder_path.set(selected)

    def _validate_fields(self, *_args):
        if self.btn_start.cget("text") == "Procesando...":
            return
        fields = [
            self.var_api_id.get().strip(),
            self.var_api_hash.get().strip(),
            self.var_channel_id.get().strip(),
            self.var_folder_path.get().strip(),
        ]
        self.btn_start.configure(state="normal" if all(fields) else "disabled")

    # ─────────── Callbacks (Observer) ───────────
    def _log(self, text: str):
        self.after(0, lambda: (self.log_view.insert("end", f"{text}\n"), self.log_view.see("end")))

    def _ask_input(self, title, prompt) -> str | None:
        dialog = LoginDialog(self, title, prompt)
        self.wait_window(dialog)
        return dialog.result

    def _request_stop(self):
        self.stop_requested = True
        self.btn_stop.configure(state="disabled", text="Cancelando...")
        self._log("!!! Cancelación solicitada.")
        self.registry.terminate()

    # ─────────── Proceso ───────────
    def _run_process(self):
        self.config.update({
            "api_id": self.var_api_id.get(),
            "api_hash": self.var_api_hash.get(),
            "channel_id": self.var_channel_id.get(),
            "folder_path": self.var_folder_path.get(),
        })
        self.mgr.save(self.config)

        self.stop_requested = False
        self.btn_start.configure(state="disabled", text="Procesando...")
        self.btn_stop.configure(state="normal", text="Detener")

        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._async_worker())

    async def _async_worker(self):
        try:
            self.telegram = TelegramService(
                self.mgr.get_session_path(),
                int(self.config["api_id"]),
                self.config["api_hash"],
            )

            if not await self.telegram.connect_and_check():
                phone = self._ask_input("Login", "Teléfono:")
                if not phone:
                    return
                await self.telegram.send_code(phone)
                code = self._ask_input("Código", "Código:")
                if not code:
                    return
                if await self.telegram.sign_in(code) == "NEED_PASSWORD":
                    pwd = self._ask_input("2FA", "Contraseña:")
                    await self.telegram.sign_in(code, password=pwd)

            # Inyección de dependencias
            sync = SyncService(
                telegram=self.telegram,
                analyzer=VideoAnalyzer(),
                thumbnail=ThumbnailGenerator(process_registry=self.registry),
                registry=self.registry,
                on_log=self._log,
                on_file_progress=lambda v: self.after(0, self.progress_bar.set, v),
                on_global_progress=lambda v: self.after(0, self.global_progress.set, v),
                stop_requested=lambda: self.stop_requested,
            )

            await sync.run(
                channel_id=int(self.config["channel_id"]),
                folder=self.config["folder_path"],
            )

            if not self.stop_requested:
                self._log("Proceso finalizado con éxito.")
                self.after(0, lambda: messagebox.showinfo("Éxito", "¡Sincronización completada!"))

        except Exception as e:
            if not self.stop_requested:
                self._log(f"Error: {e}")
                self.after(0, lambda: messagebox.showerror("Error Crítico", str(e)))
        finally:
            if self.telegram:
                await self.telegram.disconnect()
            self.after(0, self._reset_ui)

    def _reset_ui(self):
        self.btn_start.configure(text="Sincronizar y Subir")
        self.btn_stop.configure(state="disabled", text="Detener")
        self.progress_bar.set(0)
        self.global_progress.set(0)
        self._validate_fields()