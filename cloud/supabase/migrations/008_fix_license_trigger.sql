-- Fix the on-signup trigger that creates a default 'free' license.
--
-- The migration 001 version errored at runtime ("Database error saving
-- new user" on Supabase Auth signup). Two changes:
--
--   1. Schema-qualify the table (`public.licenses`). Without this,
--      the function relies on search_path, which Supabase's trigger
--      execution context doesn't always set the way you'd expect.
--
--   2. SET search_path = public, pg_temp on the function. This is the
--      pattern Supabase docs recommend for SECURITY DEFINER triggers
--      on auth.users — without it, the function's permissions
--      (running as the function owner) plus an unset search_path
--      can cause the INSERT to silently fall outside the expected
--      schema.
--
--   3. GRANT INSERT to ensure the postgres role (which Supabase Auth
--      uses for the trigger context) is allowed to write the row even
--      with RLS on. SECURITY DEFINER bypasses RLS for the row's owner
--      role, but only if the role has table-level INSERT privilege.

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP FUNCTION IF EXISTS public.create_default_license();

CREATE OR REPLACE FUNCTION public.create_default_license()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO public.licenses (user_id, tier) VALUES (NEW.id, 'free');
    RETURN NEW;
END;
$$;

-- Make sure the postgres role can write to licenses; without this,
-- the SECURITY DEFINER bypass-RLS still gets blocked by table grants.
GRANT INSERT ON public.licenses TO postgres;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.create_default_license();
