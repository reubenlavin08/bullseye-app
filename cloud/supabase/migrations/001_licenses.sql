-- Licenses table — one row per user, tracks tier + Stripe linkage.
-- Trigger creates a default 'free' license when a new auth.users row appears.

CREATE TABLE IF NOT EXISTS licenses (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    tier TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'paid', 'trial')),
    stripe_customer_id TEXT UNIQUE,
    stripe_subscription_id TEXT UNIQUE,
    current_period_end TIMESTAMPTZ,
    trial_ends_at TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_licenses_period_end ON licenses(current_period_end);

-- Auto-create free license on signup. SECURITY DEFINER lets it write
-- to licenses table even though the inserting role wouldn't normally
-- have permission.
CREATE OR REPLACE FUNCTION create_default_license()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO licenses (user_id, tier) VALUES (NEW.id, 'free');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION create_default_license();
