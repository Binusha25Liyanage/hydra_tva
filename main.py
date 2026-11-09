"""Hydra TvA — interim desktop app for the Target vs Achievement process.

Run:  python main.py
Data:  data/hydra_tva.db next to this file (or next to the .exe when packaged).
"""
import os
import sys
from pathlib import Path

import webview

from tva.api import Api


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def resource(rel: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base / rel)


def main():
    base = app_dir()
    default_out = Path(os.path.expanduser("~")) / "Documents" / "Hydra TvA Reports"
    # Admin copy (runs from source, or an admin.flag file next to the exe) can save SFA defaults.
    admin = not getattr(sys, "frozen", False) or (base / "admin.flag").exists()
    api = Api(base / "data" / "hydra_tva.db", default_out, app_dir=base, admin=admin)
    window = webview.create_window("Hydra TvA — Target vs Achievement", resource("ui/index.html"), js_api=api,
                                   width=1380, height=860, min_size=(1100, 700), background_color="#F4F1EA")
    api._window = window
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
