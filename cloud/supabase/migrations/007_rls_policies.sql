-- Row-level security: every user-owned table can only be read/written
-- by the row's owner. Without RLS, the anon key (which we ship in the
-- desktop bundle) would let any client read any row.

ALTER TABLE licenses          ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_watches      ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_log         ENABLE ROW LEVEL SECURITY;
ALTER TABLE email_queue       ENABLE ROW LEVEL SECURITY;
ALTER TABLE telemetry_events  ENABLE ROW LEVEL SECURITY;

-- comps_cache is intentionally NOT RLS'd — it's a global shared cache,
-- and the access pattern is "any authenticated user can read any row,
-- only Edge Functions (service-role key) can write."
ALTER TABLE comps_cache       ENABLE ROW LEVEL SECURITY;

-- Licenses: read-only from client; writes only via service role
-- (Stripe webhook, license trigger).
CREATE POLICY "Users see own license"
    ON licenses FOR SELECT
    USING (auth.uid() = user_id);

-- Watches: full CRUD by owner.
CREATE POLICY "Users manage own watches"
    ON user_watches FOR ALL
    USING (auth.uid() = user_id);

-- Email log: read-only from client.
CREATE POLICY "Users see own email log"
    ON email_log FOR SELECT
    USING (auth.uid() = user_id);

-- Email queue: read-only from client (writes from Edge Functions).
CREATE POLICY "Users see own email queue"
    ON email_queue FOR SELECT
    USING (auth.uid() = user_id);

-- Telemetry: clients can INSERT only. They never read their own events.
CREATE POLICY "Users insert own telemetry"
    ON telemetry_events FOR INSERT
    WITH CHECK (auth.uid() = user_id OR user_id IS NULL);

-- Comps cache: any authenticated user can SELECT (it's a shared resource).
CREATE POLICY "Authenticated users read comps cache"
    ON comps_cache FOR SELECT
    USING (auth.role() = 'authenticated');
