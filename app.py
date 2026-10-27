"""
HIDRO360 (TEAM VIFY AI) - punto de entrada de la interfaz (Flet).

    python app.py                      # ventana de escritorio
    python app.py --web                # navegador en http://127.0.0.1:8501
    python app.py --web --host 0.0.0.0 --sin-navegador   # servidor para la red local (solo red de confianza: el canal no está cifrado)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from interface.flet_app.main import run  # noqa: E402

if __name__ == "__main__":
    run()
