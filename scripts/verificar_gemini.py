"""
Verifica la configuración de Gemini SIN mostrar la clave.

    python -m scripts.verificar_gemini                # configuración + lista de modelos + una llamada mínima
    python -m scripts.verificar_gemini --sin-llamada  # solo configuración (no usa red ni cuota)

Códigos de salida: 0 = todo bien · 1 = fallo de conexión/credenciales/modelo · 2 = Gemini no configurado
(el sistema sigue funcionando en modo local).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from services import ia_gemini  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-llamada", action="store_true")
    args = ap.parse_args()

    print("Proveedor efectivo      :", config.ai_provider(), "(AI_PROVIDER =", config._env("AI_PROVIDER", "auto") + ")")
    print("Modelo (GEMINI_MODEL)   :", config.GEMINI_MODEL())
    print("Clave configurada       :", "sí" if config.GEMINI_API_KEY() else "NO")
    print("Tiempo máximo (s)       :", config.AI_TIMEOUT_SECONDS(), "· reintentos:", config.AI_MAX_REINTENTOS())
    if not config.GEMINI_API_KEY():
        print("\nGemini no está configurado: el sistema usa el modo local por reglas (es válido).")
        print("Para activarlo: cree .env a partir de .env.example y fije GEMINI_API_KEY (ver docs/IA_GEMINI.md).")
        return 2
    if args.sin_llamada:
        print("\nConfiguración presente. No se hizo ninguna llamada (--sin-llamada).")
        return 0
    try:
        modelos = ia_gemini.listar_modelos()
        print(f"\nConexión y clave válidas: {len(modelos)} modelos con generateContent.")
        if config.GEMINI_MODEL() not in modelos:
            print(f"AVISO: '{config.GEMINI_MODEL()}' no aparece en la lista. Modelos que empiezan por 'gemini':")
            for m in [m for m in modelos if m.startswith("gemini")][:15]:
                print("  -", m)
            print("Cambie GEMINI_MODEL en .env por uno de la lista.")
            return 1
        r = json.loads(ia_gemini.generar_json('Responde únicamente con el JSON {"ok": true}', "ping", tarea="verificacion"))
        print("Llamada de prueba correcta:", r)
        return 0
    except Exception as e:
        print(f"\nFALLO: {type(e).__name__}: {e}")
        print("Revise la clave, el nombre del modelo, la conexión y la cuota (AI Studio). El sistema seguirá en modo local.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
