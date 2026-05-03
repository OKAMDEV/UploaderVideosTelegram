import sys

from src import AppGui, ConfigManager


def main():
    mgr = ConfigManager()
    app = AppGui(mgr)
    try:
        app.mainloop()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"Error al iniciar la aplicación: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()