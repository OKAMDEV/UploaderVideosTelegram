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
        # Ventana de login no redimensionable
        self.resizable(False, False)
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
        
        self.title("Uploader Videos Telegram")
        self.geometry("650x650")
        # Ventana principal no redimensionable
        self.resizable(False, False)

        ctk.CTkLabel(self, text="Configuración", font=("Roboto", 22)).pack(pady=20)
        
        # Variables de control para validación en tiempo real
        self.var_api_id = ctk.StringVar(value=str(self.config.get("api_id", "")))
        self.var_api_hash = ctk.StringVar(value=self.config.get("api_hash", ""))
        self.var_channel_id = ctk.StringVar(value=str(self.config.get("channel_id", "")))
        self.var_folder_path = ctk.StringVar(value=self.config.get("folder_path", ""))

        # Escuchar cambios en cada campo
        for var in [self.var_api_id, self.var_api_hash, self.var_channel_id, self.var_folder_path]:
            var.trace_add("write", self.validate_fields)

        # Creación de campos vinculados a las variables
        self.entry_api_id = self.create_field("API ID", self.var_api_id)
        self.entry_api_hash = self.create_field("API Hash", self.var_api_hash)
        self.entry_channel_id = self.create_field("Channel ID", self.var_channel_id)
        self.entry_folder_path = self.create_field("Carpeta Videos", self.var_folder_path)

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

        # Validación inicial al arrancar
        self.validate_fields()

    def create_field(self, label, variable):
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=40, pady=5)

        ctk.CTkLabel(frame, text=label, width=120, anchor="w").pack(side="left")

        entry = ctk.CTkEntry(frame, textvariable=variable)
        entry.pack(side="right", fill="x", expand=True)

        return entry

    def validate_fields(self, *args):
        """Habilita el botón solo si todos los campos tienen texto."""
        fields = [
            self.var_api_id.get().strip(),
            self.var_api_hash.get().strip(),
            self.var_channel_id.get().strip(),
            self.var_folder_path.get().strip()
        ]
        
        if all(fields):
            self.btn_start.configure(state="normal")
        else:
            self.btn_start.configure(state="disabled")

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
        # Actualizamos config con los valores actuales de las variables
        self.config.update({
            "api_id": self.var_api_id.get(),
            "api_hash": self.var_api_hash.get(),
            "channel_id": self.var_channel_id.get(),
            "folder_path": self.var_folder_path.get()
        })

        self.mgr.save(self.config)

        self.btn_start.configure(state="disabled", text="Procesando...")
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.async_worker())

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
                if not phone: return # Cancelado
                await service.send_code(phone)

                code = self.ask_input("Código", "Ingresa el código:")
                if not code: return
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
                if self.normalizar(nombre) not in remotos_norm:
                    pendientes.append(f)

            total = len(pendientes)
            if total == 0:
                self.write_log("No hay videos nuevos para subir.")
                return

            for i, f in enumerate(pendientes, start=1):
                self.write_log(f"Subiendo [{i}/{total}]: {f}")

                def progress_cb(current, total_bytes):
                    self.after(0, self.progress_bar.set, current / total_bytes)

                await service.upload_file(cid, os.path.join(ruta, f), progress_cb)

                self.after(0, self.progress_bar.set, 0)
                self.after(0, self.global_progress.set, i / total)
                self.write_log(f"Completado: {f}")

            self.write_log("Proceso finalizado con éxito.")

        except Exception as e:
            self.write_log(f"Error crítico: {e}")
        finally:
            if service:
                await service.disconnect()
            
            # Restaurar estado de la UI
            self.after(0, lambda: (
                self.btn_start.configure(text="Sincronizar y Subir"),
                self.validate_fields(), # Re-valida para habilitar si los campos siguen llenos
                self.progress_bar.set(0),
                self.global_progress.set(0)
            ))