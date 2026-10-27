-- HIDRO360 - Migracion 2/3: la auditoria es append-only tambien en la base de datos.
--
-- Un trigger rechaza UPDATE, DELETE y TRUNCATE sobre `auditoria` para CUALQUIER rol (incluido el dueno de la
-- tabla), con una unica excepcion: DELETE/TRUNCATE si la transaccion fija la variable local
-- hidro360.permitir_reinicio = 'si'. Solo la usa data/database.py::vaciar() para "Cargar datos de ejemplo" (reinicio
-- de la DEMO) y nunca con HIDRO360_ENV=prod. UPDATE no tiene excepcion alguna.
--
-- QUE NO IMPIDE (honestidad): un superusuario o el dueno de la tabla puede desactivar o borrar el trigger
-- (ALTER TABLE ... DISABLE TRIGGER / DROP TRIGGER), y cualquier rol que pueda ejecutar SET LOCAL puede abrir la
-- excepcion de reinicio. La barrera real para el rol de la aplicacion (hidro360_app, migracion 3) es que no tiene
-- privilegios UPDATE/DELETE/TRUNCATE sobre la tabla; el trigger es la segunda capa. La deteccion de alteraciones
-- por quien tenga control total de la base sigue siendo la cadena de hashes (services/audit.py::verificar_integridad).

CREATE OR REPLACE FUNCTION auditoria_inmutable() RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF TG_OP = 'DELETE' AND current_setting('hidro360.permitir_reinicio', true) = 'si' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'TRUNCATE' AND current_setting('hidro360.permitir_reinicio', true) = 'si' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION 'auditoria es append-only: % denegado', TG_OP USING ERRCODE = 'insufficient_privilege';
END
$$;

DROP TRIGGER IF EXISTS auditoria_sin_modificar ON auditoria;
CREATE TRIGGER auditoria_sin_modificar
    BEFORE UPDATE OR DELETE ON auditoria
    FOR EACH ROW EXECUTE FUNCTION auditoria_inmutable();

DROP TRIGGER IF EXISTS auditoria_sin_truncar ON auditoria;
CREATE TRIGGER auditoria_sin_truncar
    BEFORE TRUNCATE ON auditoria
    FOR EACH STATEMENT EXECUTE FUNCTION auditoria_inmutable();
