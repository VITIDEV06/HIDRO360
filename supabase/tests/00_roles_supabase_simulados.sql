-- SOLO PARA PRUEBAS EN UN POSTGRES PLANO (no se aplica en Supabase, donde estos roles ya existen).
-- Imita lo esencial de los roles de Supabase para poder probar RLS y permisos:
--   anon            : peticiones anonimas a la API de Supabase (PostgREST)
--   authenticated   : usuarios con sesion de Supabase Auth
--   service_role    : clave de servicio; salta RLS (BYPASSRLS)
--   authenticator   : rol de conexion de PostgREST (cambia a anon/authenticated)
-- El rol de la aplicacion (hidro360_app) lo crea la migracion de seguridad, no este script.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        CREATE ROLE anon NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        CREATE ROLE authenticated NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticator') THEN
        CREATE ROLE authenticator NOINHERIT LOGIN;
    END IF;
END
$$;
GRANT anon, authenticated, service_role TO authenticator;
-- Supabase concede por defecto privilegios amplios sobre `public` a estos roles: se imita para que las pruebas
-- demuestren que la migracion de seguridad los revoca de verdad.
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO anon, authenticated, service_role;
