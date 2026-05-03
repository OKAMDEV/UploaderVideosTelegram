import os
import sys


def app_root() -> str:
    """
    Retorna la raíz del proyecto tanto en desarrollo como en el .exe.

    - En desarrollo: carpeta que contiene main.py
    - En PyInstaller --onefile: carpeta temporal _MEIPASS
    - En PyInstaller --onedir: carpeta del ejecutable
    """
    if getattr(sys, "frozen", False):
        # PyInstaller: _MEIPASS apunta a los recursos empaquetados
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # Desarrollo: src/paths.py  -> subimos uno para llegar a la raíz
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(relative: str) -> str:
    """Resuelve rutas a recursos (assets, íconos, etc.)."""
    return os.path.join(app_root(), relative)


def user_data_path() -> str:
    """
    Carpeta donde se guardan config.json y sesión de Telegram.

    En el .exe NO usamos _MEIPASS (es temporal y de solo lectura),
    sino la carpeta real del ejecutable.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))