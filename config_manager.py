import json
import os
import sys

class ConfigManager:
    def __init__(self):
        if getattr(sys, 'frozen', False):
            self.base_path = os.path.dirname(sys.executable)
        else:
            self.base_path = os.path.dirname(__file__)
        
        self.config_file = os.path.join(self.base_path, "config.json")
        self.session_path = os.path.join(self.base_path, "sesion_subida")

    def load(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def save(self, data):
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    def get_session_path(self):
        return self.session_path