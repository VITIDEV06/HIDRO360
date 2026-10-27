---
name: hidro360-flet-ui
description: Usar al crear o modificar pantallas, componentes o navegación de la interfaz Flet de HIDRO360 (interface/flet_app). Aplica la identidad visual del prototipo y del logo, separa UI de lógica de negocio y exige estados de carga/error/éxito y pruebas de interacción.
---

# Interfaz Flet de HIDRO360

Versión fijada: **flet 1.0.4** (API distinta de 0.x: `ft.run`, `page.show_dialog/pop_dialog`, `ft.Dropdown(on_select=...)`, `ft.DropdownOption`, `ft.Button`, `ft.Padding.symmetric`, `ft.Border.all`, `ft.Alignment.CENTER`).

## Estructura
- `interface/flet_app/main.py` carcasa (barra lateral, barra superior, registro `PAGINAS`) y `run()`.
- `portal/<modulo>.py` (público) y `admin/<modulo>.py` (administración) exportan `build(ctx) -> ft.Control`. Se registra en `PAGINAS` (clave, etiqueta, icono, módulo).
- `components.py` componentes (panel, kpi, pill, nota, tabla, barra, boton, campo, lista_desplegable…); `theme.py` paleta; `contexto.py` `Ctx`.
- Identidad: la paleta vive en `theme.py` (navy `#09265d`, azul `#087cf0`, cian `#15c9e8`, tarjetas redondeadas blancas, hero degradado; diseño documentado en `docs/DISENO_UI.md`) y el logo en `assets/logo.jpeg` (**JPEG sobre fondo blanco, sin transparencia: úselo solo sobre superficies blancas; no lo edite**). Nombre del producto: «HIDRO360» (constante `config.APP_NAME`).

## Reglas
1. **UI sin lógica de negocio**: llamar a `services.workflow` solo mediante `ctx.ejecutar(lambda: workflow.xxx(...), "mensaje de éxito")`; esto traduce `ErrorDeNegocio` en un aviso rojo claro. Nunca SQLite desde una página.
2. **Estados**: carga (`C.cargando` + deshabilitar el botón mientras se ejecuta), error (aviso/`C.nota` rojo con texto comprensible), éxito (toast). Formularios: valide lo obvio en la página (vacío, número) y deje que el backend valide las reglas.
3. **Acciones sensibles o destructivas** (cargar demo, aprobar, rechazar) → `ctx.confirmar(...)`.
4. **Datos ficticios** siempre identificados (`C.ficticio()`).
5. Un error al construir una página no debe tumbar la app: `construir_pagina` ya lo captura; no lo eluda.
6. No cree pantallas vacías: una página existe solo si hace algo real.

## Trampas conocidas de Flet 1.0 (verificadas en este proyecto)
- `expand=True` en hijos de un `Row(wrap=True)` rompe el layout (Flutter pinta un recuadro gris): use ancho fijo (`C.kpi(ancho=232)`).
- Un `TextField` dentro de una `Column` no ocupa todo el ancho: póngalo en `ft.Row([campo])` con `expand=True`.
- `run(..., no_cdn=True)` en modo web deja la página en blanco (faltan fuentes locales). No lo use. La primera carga en `--web` tarda ~15-20 s.
- Botones: el texto está en `.content`; los manejadores sync se ejecutan en hilo: actualice con `ctx.actualizar()`.
- Sin fuentes remotas en el tema (la app debe funcionar sin internet en escritorio).

## Pruebas
`tests/test_flet.py` construye las páginas con `Ctx(None)` y llama a los manejadores (`boton(p, "texto").on_click(None)`, `campo(p, "etiqueta").value = ...`). Añada una prueba por acción nueva que persista datos. Estas pruebas **no** sustituyen mirar la app: arranque `python app.py --web --sin-navegador --port 8503` y revise en el navegador (ancho ≥ 980 px = barra lateral completa; menor = compacta).

## Verificación
`pytest -q tests/test_flet.py` verde · cada página construye sin «No se pudo mostrar» · revisión visual en el navegador de al menos la página tocada · accesibilidad: contraste de texto ≥ AA sobre fondos de `theme.py`, tooltips en iconos, etiquetas en campos.
