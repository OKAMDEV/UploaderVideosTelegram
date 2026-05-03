import json
import os

from .paths import user_data_path


class ConfigManager:
    """Gestiona la persistencia de configuración en config.json."""

    def __init__(self):
        self.base_path = user_data_path()
        self.config_file = os.path.join(self.base_path, "config.json")
        self.session_path = os.path.join(self.base_path, "sesion_subida")

    def load(self) -> dict:
        if not os.path.exists(self.config_file):
            return {}
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error al cargar config: {e}")
            return {}

    def save(self, data: dict) -> None:
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error al guardar config: {e}")

    def get_session_path(self) -> str:
        return self.session_path