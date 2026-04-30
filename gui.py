import customtkinter as ctk
import threading
import asyncio
import os
import unicodedata
from uploader_core import TelegramService

class LoginDialog(ctk.CTkToplevel):
    def __init__(self, master, title, prompt):
        super().__init__(master)
        self.title(title)
        self.geometry("300x180")
        self.result = None
        
        ctk.CTkLabel(self, text=prompt, wraplength=250).pack(pady=10)
        
        self.entry = ctk.CTkEntry(self)
        self.entry.pack(pady=10)
        self.entry.bind("<Return>", lambda e: self.on_ok())
        
        ctk.CTkButton(self, text="Aceptar", command=self.on_ok).pack(pady=10)
        self.grab_set()

    def on_ok(self):
        self.result = self.entry.get()
        self.destroy()


class AppGui(ctk.CTk):
    def __init__(self, config_manager):
        super().__init__()
        self.mgr = config_manager
        self.config = self.mgr.load()
        
        self.title("Telegram Cloud Respaldo - Pro")
        self.geometry("650x650")

        ctk.CTkLabel(self, text="Configuración de Almacenamiento", font=("Roboto", 22)).pack(pady=20)
        
        self.api_id = self.create_field("API ID", self.config.get("api_id", ""))
        self.api_hash = self.create_field("API Hash", self.config.get("api_hash", ""))
        self.channel_id = self.create_field("Channel ID", self.config.get("channel_id", ""))
        self.folder_path = self.create_field("Carpeta Videos", self.config.get("folder_path", ""))

        self.btn_start = ctk.CTkButton(self, text="Sincronizar y Subir", command=self.run_process)
        self.btn_start.pack(pady=20)

        self.log_view = ctk.CTkTextbox(self)
        self.log_view.pack(padx=20, pady=10, fill="both", expand=True)

        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.pack(fill="x", padx=20, pady=5)
        self.progress_bar.set(0)

        self.global_progress = ctk.CTkProgressBar(self)
        self.global_progress.pack(fill="x", padx=20, pady=(0,10))
        self.global_progress.set(0)

    def create_field(self, label, value):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=40, pady=5)

        ctk.CTkLabel(frame, text=label, width=120, anchor="w").pack(side="left")

        entry = ctk.CTkEntry(frame)
        entry.insert(0, str(value))
        entry.pack(side="right", fill="x", expand=True)

        return entry

    def write_log(self, text):
        self.after(0, lambda: (
            self.log_view.insert("end", f"{text}\n"),
            self.log_view.see("end")
        ))

    def ask_input(self, title, prompt):
        dialog = LoginDialog(self, title, prompt)
        self.wait_window(dialog)
        return dialog.result

    def run_process(self):
        self.config.update({
            "api_id": self.api_id.get(),
            "api_hash": self.api_hash.get(),
            "channel_id": self.channel_id.get(),
            "folder_path": self.folder_path.get()
        })

        self.mgr.save(self.config)

        self.btn_start.configure(state="disabled", text="Procesando...")
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.async_worker())

    # 🔥 normalización real
    def normalizar(self, texto):
        texto = unicodedata.normalize("NFKD", texto)
        return (
            texto.strip()
            .lower()
            .replace(".mp4", "")
            .replace("  ", " ")
        )

    async def async_worker(self):
        service = None

        try:
            service = TelegramService(
                self.mgr.get_session_path(),
                int(self.config["api_id"]),
                self.config["api_hash"]
            )

            if not await service.connect_and_check():
                phone = self.ask_input("Login", "Ingresa tu teléfono:")
                await service.send_code(phone)

                code = self.ask_input("Código", "Ingresa el código:")
                res = await service.sign_in(code)

                if res == "NEED_PASSWORD":
                    pwd = self.ask_input("2FA", "Contraseña:")
                    await service.sign_in(code, password=pwd)

            cid = int(self.config["channel_id"])

            remotos = await service.get_remote_captions(cid)
            remotos_norm = {self.normalizar(c) for c in remotos}

            ruta = self.config["folder_path"]
            locales = [f for f in os.listdir(ruta) if f.lower().endswith(".mp4")]

            pendientes = []

            for f in locales:
                nombre = os.path.splitext(f)[0]
                local_norm = self.normalizar(nombre)

                if local_norm in remotos_norm:
                    continue

                pendientes.append(f)

            total = len(pendientes)

            for i, f in enumerate(pendientes, start=1):
                self.write_log(f"Subiendo: {f}")

                def progress_cb(current, total_bytes):
                    self.after(0, self.progress_bar.set, current / total_bytes)

                await service.upload_file(cid, os.path.join(ruta, f), progress_cb)

                self.after(0, self.progress_bar.set, 0)
                self.after(0, self.global_progress.set, i / total)

                self.write_log(f"Completado: {f}")

            self.write_log("Todo listo.")

        except Exception as e:
            self.write_log(f"Error crítico: {e}")

        finally:
            if service:
                await service.disconnect()

            self.after(0, lambda: self.btn_start.configure(state="normal", text="Sincronizar y Subir"))
            self.after(0, lambda: self.progress_bar.set(0))
            self.after(0, lambda: self.global_progress.set(0))