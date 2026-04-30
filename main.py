from config_manager import ConfigManager
from gui import AppGui

def main():
    mgr = ConfigManager()
    app = AppGui(mgr)
    app.mainloop()

if __name__ == "__main__":
    main()