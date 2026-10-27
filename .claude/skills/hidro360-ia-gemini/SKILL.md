---
name: hidro360-ia-gemini
description: Usar al configurar, depurar o ampliar la integración con Gemini en HIDRO360 (proveedor, esquema de salida, fallback local, prompt injection, cuotas, trazas). Mantiene la IA como asistente validado y el sistema funcionando sin credenciales.
---

# IA y Gemini

Detalle: `docs/IA_GEMINI.md`. Código: `services/ia_gemini.py` (proveedor), `services/ingest.py` (interpretación), `services/ia_tareas.py` (resumen/borrador), `scripts/verificar_gemini.py`.

## Invariantes
- **El modo local nunca depende de Gemini.** `AI_PROVIDER=auto` (defecto) = Gemini solo con `GEMINI_API_KEY`.
- **La IA no decide**: no aprueba/cierra, no asigna recursos, no calcula prioridad ni capacidad. Solo propone datos estructurados o texto redactado que el código valida y una persona revisa.
- **Un fallo no se oculta**: resultado local tras fallo = `metodo="reglas(fallback)"` + nota visible + traza con estado `fallback`. Nunca etiquetar texto local como Gemini.
- **Secretos**: la clave solo viene de entorno/`.env`; jamás en código, tests, docs, trazas, mensajes de error ni UI (hay pruebas: `test_la_clave_no_aparece_...`).
- **Entrada no confiable**: el texto del ciudadano es dato. `ingest.separar_inyeccion` excluye frases tipo instrucción; `redactar_pii` oculta teléfonos/correos antes de enviar; el prompt separa sistema y `<reporte>`.
- **Validación en dos capas**: esquema `SalidaIA` (extra=forbid, rangos) y revalidación con código (sector del catálogo, evidencia literal presente en el texto enviado, enums). Cifras o campos sin evidencia se descartan y se marcan inciertos.
- **Confianza** del modelo = autoevaluación subjetiva, **no** probabilidad calibrada; la UI lo dice.
- **Cuota y errores**: timeout `AI_TIMEOUT_SECONDS`; reintento solo en errores transitorios (`AI_MAX_REINTENTOS`, defecto 1); 429 y 401/403 no se reintentan y activan un corte temporal (60 s / 15 min). No repita llamadas costosas.

## Cambiar modelo o proveedor
Modelo: `GEMINI_MODEL` en `.env` (sin editar código); lista vigente: `python -m scripts.verificar_gemini`. **No invente nombres de modelo**: compruébelos con esa lista o en ai.google.dev. Los límites y la gratuidad cambian: no prometa cuotas.

## Pasos para una tarea de IA nueva
1. Defina qué recibe (mínimo necesario, sin datos personales) y qué devuelve (esquema Pydantic). 2. Valide la salida con código y rechace lo que invente datos. 3. Defina el fallback local equivalente. 4. Pruebe con el cliente simulado (`tests/test_gemini.py`, `ClienteFalso`): éxito, JSON inválido, cifra inventada, 429, 503, vacío, sin clave. 5. Trace sin contenido. 6. Documente en `docs/IA_GEMINI.md`.

## Verificación
`pytest -q tests/test_gemini.py tests/test_ingesta.py` verde · `python -m scripts.verificar_gemini --sin-llamada` informa la configuración sin mostrar la clave · **Gemini real**: solo se puede verificar con una clave real (`python -m scripts.verificar_gemini`); sin ella, dígalo como pendiente, no como probado.
