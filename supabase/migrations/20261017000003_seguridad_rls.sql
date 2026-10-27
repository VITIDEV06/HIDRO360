-- HIDRO360 - Migracion 3/3: Row Level Security y privilegios.
--
-- Modelo: toda la logica y los permisos viven en el BACKEND, que se conecta directamente a Postgres (pooler de
-- Supabase) con un rol de servidor. Los roles de la API de Supabase (PostgREST) NO deben tocar estas tablas:
--   * RLS ACTIVADA en todas las tablas, SIN politicas para anon/authenticated  -> por RLS no ven ni escriben nada.
--   * REVOKE ALL a anon/authenticated sobre tablas, secuencias y funciones      -> ni siquiera llegan a RLS.
--   * Privilegios por defecto revocados, para que tablas futuras tampoco queden abiertas.
-- Se aplican las dos barreras a proposito (defensa en profundidad: si alguien hiciera un GRANT por error, RLS sigue
-- negando; si alguien creara una politica por error, faltaria el privilegio).
--
-- No se crea NINGUNA politica para anon/authenticated: no hay un acceso directo que lo justifique (los avisos
-- publicos y la consulta por codigo pasan por la API del backend, que valida y limita la tasa).
--
-- Rol de aplicacion `hidro360_app` (NOLOGIN al crearlo; el operador le pone LOGIN y contrasena fuera del repositorio):
-- privilegio minimo, sujeto a RLS con politicas propias; en `auditoria` solo SELECT e INSERT (sin UPDATE/DELETE/TRUNCATE).
-- El dueno de las tablas (en Supabase, `postgres`) y service_role se saltan RLS por diseno de Postgres/Supabase: son
-- credenciales de administracion, no deben usarse para atender al publico y nunca llegan al navegador.

DO $$
DECLARE
    esq    text := current_schema();
    t      text;
    r      text;
    roles_api text[] := ARRAY[]::text[];
    tablas text[] := ARRAY['schema_migraciones', 'sectores', 'cisternas', 'reportes', 'planes', 'decisiones_duplicado',
                           'auditoria', 'usuarios', 'sesiones', 'limites', 'fotos', 'dispositivos', 'misiones',
                           'eventos_mision', 'recomendaciones'];
BEGIN
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            roles_api := roles_api || r;
        END IF;
    END LOOP;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hidro360_app') THEN
        CREATE ROLE hidro360_app NOLOGIN;
    END IF;

    FOREACH t IN ARRAY tablas LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', esq, t);
        EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM PUBLIC', esq, t);
        IF array_length(roles_api, 1) IS NOT NULL THEN
            EXECUTE format('REVOKE ALL ON TABLE %I.%I FROM %s', esq, t, (SELECT string_agg(quote_ident(x), ', ') FROM unnest(roles_api) x));
        END IF;
    END LOOP;

    IF array_length(roles_api, 1) IS NOT NULL THEN
        EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %s', esq, (SELECT string_agg(quote_ident(x), ', ') FROM unnest(roles_api) x));
        EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM %s', esq, (SELECT string_agg(quote_ident(x), ', ') FROM unnest(roles_api) x));
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON TABLES FROM %s', esq, (SELECT string_agg(quote_ident(x), ', ') FROM unnest(roles_api) x));
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON SEQUENCES FROM %s', esq, (SELECT string_agg(quote_ident(x), ', ') FROM unnest(roles_api) x));
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON FUNCTIONS FROM %s', esq, (SELECT string_agg(quote_ident(x), ', ') FROM unnest(roles_api) x));
    END IF;
    EXECUTE format('REVOKE ALL ON FUNCTION %I.auditoria_inmutable() FROM PUBLIC', esq);

    -- ---- rol de aplicacion: privilegio minimo + politicas propias
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO hidro360_app', esq);
    FOREACH t IN ARRAY tablas LOOP
        IF t = 'auditoria' THEN
            EXECUTE format('GRANT SELECT, INSERT ON TABLE %I.%I TO hidro360_app', esq, t);
        ELSIF t = 'schema_migraciones' THEN
            EXECUTE format('GRANT SELECT ON TABLE %I.%I TO hidro360_app', esq, t);
        ELSE
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.%I TO hidro360_app', esq, t);
        END IF;
    END LOOP;
    -- UPDATE sobre secuencias: vaciar() reinicia los contadores con setval() al recargar la demo.
    EXECUTE format('GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO hidro360_app', esq);

    FOREACH t IN ARRAY tablas LOOP
        EXECUTE format('DROP POLICY IF EXISTS app_total ON %I.%I', esq, t);
        EXECUTE format('DROP POLICY IF EXISTS app_leer ON %I.%I', esq, t);
        EXECUTE format('DROP POLICY IF EXISTS app_insertar ON %I.%I', esq, t);
        IF t = 'auditoria' THEN
            EXECUTE format('CREATE POLICY app_leer ON %I.%I FOR SELECT TO hidro360_app USING (true)', esq, t);
            EXECUTE format('CREATE POLICY app_insertar ON %I.%I FOR INSERT TO hidro360_app WITH CHECK (true)', esq, t);
        ELSIF t = 'schema_migraciones' THEN
            EXECUTE format('CREATE POLICY app_leer ON %I.%I FOR SELECT TO hidro360_app USING (true)', esq, t);
        ELSE
            EXECUTE format('CREATE POLICY app_total ON %I.%I FOR ALL TO hidro360_app USING (true) WITH CHECK (true)', esq, t);
        END IF;
    END LOOP;
END
$$;
