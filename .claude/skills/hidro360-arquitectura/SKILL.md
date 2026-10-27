---
name: hidro360-arquitectura
description: Usar antes de cambiar la estructura de HIDRO360, mover o retirar código, añadir un módulo o planificar una migración. Inspecciona el repo, protege cambios existentes, evita duplicación y retira código obsoleto solo con evidencia.
---

# Arquitectura y mantenimiento de HIDRO360

Léase primero `AGENTS.md` (arquitectura real) y `docs/DECISIONES.md` (por qué es así). Esta skill es el procedimiento, no una copia de ese contenido.

## Cuándo activarla
Antes de: añadir un módulo, mover archivos, cambiar un contrato, retirar código o dependencias, migrar algo (UI, proveedor, esquema).

## Pasos
1. **Estado**: `git status --short` y `git log --oneline -5`. Hay trabajo sin confirmar del usuario: no lo descarte (`git checkout`, `reset`, `clean` están prohibidos sin permiso). Si el cambio es grande, haga un respaldo (zip del árbol sin `.venv`/`.git`) antes de tocar nada.
2. **Mapa de uso**: `git grep -n --untracked "<símbolo>" -- . ":!.venv"` para imports directos e indirectos (cadenas, nombres en docs, `importlib`). El registro de páginas Flet se carga con `importlib` (`interface/flet_app/main.py: PAGINAS`): un archivo de `portal/` o `admin/` sin import directo **no** es huérfano.
3. **¿Ya existe?** Busque una abstracción equivalente antes de crear otra (p. ej. la costura de IA es `ingest.transporte_ia`; los avisos de UI son `Ctx.toast`).
4. **Capas**: la regla de negocio va en `services/`; UI y API solo llaman a `services.workflow`. Si una regla aparece en una página Flet o en `api/main.py`, muévala.
5. **Cambiar con pruebas**: añada o adapte pruebas; ejecute `pytest -q`. Cambios de reglas → subir `VERSION_REGLAS`; de contrato → `SCHEMA_VERSION`.
6. **Migración segura**: mantenga lo viejo funcionando hasta verificar que lo nuevo cubre cada responsabilidad (haga una tabla viejo→nuevo). Solo entonces retire, y quite también dependencias, scripts, pruebas obsoletas y docs.
7. **Retirar código/archivos** solo si: sin referencias · responsabilidad cubierta y probada · no es integración futura documentada · recursos importantes preservados (duplicados: compare SHA-256, no solo nombre/tamaño) · docs actualizadas. Con dudas, conserve y anótelo en `docs/DECISIONES.md`. Nunca borre bases de datos, secretos ni archivos del usuario.
8. **Documentar**: `CHANGELOG.md`, el doc afectado en `docs/`, y `AGENTS.md` si cambió la arquitectura.

## Restricciones
No cree archivos «para aparentar»; cada archivo nuevo necesita un uso real y una prueba. No afirme que algo funciona sin haberlo ejecutado. Datos ficticios siempre identificados.

## Verificación
`pytest -q` verde; `git grep` sin referencias colgantes tras un retiro; `python -c "import app"` importa; `AGENTS.md` coincide con el árbol real.
