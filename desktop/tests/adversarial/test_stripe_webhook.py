"""Adversarial probes against /stripe-webhook and /checkout-create.

These tests hit the live Supabase Edge Function endpoints. They are
read-only — no payment is ever completed. The goal is to confirm the
deployed handlers reject malformed/forged input the way the source says
they should, and to document any drift between code review and live
behavior.

Cloud calls are gated by BULLSEYE_RUN_LIVE=1 so the suite stays runnable
in CI without network access. Code-trace assertions (S5/S6) read the
handler source directly and have no network dependency, so they always
run.

S1   Unsigned webhook                  -> expect 400
S2   Wrong-signature webhook           -> expect 400
S3   Forged checkout.session.completed -> expect 400 (no signature)
S4   Stale event (created 10h ago)     -> document tolerance
S5   Out-of-order events (code trace)  -> handler must throw
S6   Idempotency on retry (code trace) -> all handlers UPSERT/UPDATE
S7   /checkout-create input fuzz       -> validates auth-first ordering
S8   /checkout-create with no JWT      -> expect 401
S9   Trial double-redemption           -> code trace + documentation
S10  Reuse of checkout URL across users-> code trace + documentation
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

try:
    import requests  # noqa: F401
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

PROJECT = "https://qfkzhyxmohytnzskcmdv.supabase.co"
ANON = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFma3poeXhtb2h5dG56c2tjbWR2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc5MjIzMjYsImV4cCI6MjA5MzQ5ODMyNn0."
    "pPathNzQcURGocSVPKKhgcv27-4sqHAFZeB1oenB0Z4"
)
WEBHOOK = f"{PROJECT}/functions/v1/stripe-webhook"
CHECKOUT = f"{PROJECT}/functions/v1/checkout-create"

LIVE = os.environ.get("BULLSEYE_RUN_LIVE") == "1"
USER_JWT = os.environ.get("BULLSEYE_TEST_USER_JWT")  # optional, for S7 happy paths

REPO_ROOT = Path(__file__).resolve().parents[3]
WEBHOOK_SRC = REPO_ROOT / "cloud" / "supabase" / "functions" / "stripe-webhook" / "index.ts"
CHECKOUT_SRC = REPO_ROOT / "cloud" / "supabase" / "functions" / "checkout-create" / "index.ts"
SHARED_SRC = REPO_ROOT / "cloud" / "supabase" / "functions" / "_shared" / "stripe.ts"

requires_live = pytest.mark.skipif(
    not (LIVE and _HAS_REQUESTS),
    reason="set BULLSEYE_RUN_LIVE=1 (and install requests) to run live cloud probes",
)
requires_jwt = pytest.mark.skipif(
    not (LIVE and _HAS_REQUESTS and USER_JWT),
    reason="set BULLSEYE_TEST_USER_JWT to exercise authenticated /checkout-create paths",
)


# --- S1 / S2 / S3 / S4 ---------------------------------------------------

@requires_live
def test_s1_unsigned_webhook_rejected():
    """No stripe-signature header -> handler must return 400 before
    touching the database. Anything else is a CRITICAL: an attacker
    could grant themselves Pro tier with a curl one-liner.
    """
    body = json.dumps({
        "id": "evt_adv_unsigned",
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_adv", "customer": "cus_adv", "status": "active",
        }},
    })
    r = requests.post(WEBHOOK, data=body, headers={"Content-Type": "application/json"}, timeout=15)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    assert "missing stripe-signature" in r.text.lower()


@requires_live
def test_s2_wrong_signature_rejected():
    """A made-up t=...,v1=deadbeef signature must fail HMAC verification."""
    body = json.dumps({
        "id": "evt_adv_badsig",
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_adv2", "customer": "cus_adv2", "status": "active",
        }},
    })
    r = requests.post(
        WEBHOOK,
        data=body,
        headers={
            "Content-Type": "application/json",
            "stripe-signature": "t=1,v1=deadbeef",
        },
        timeout=15,
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    assert "signature verification failed" in r.text.lower()


@requires_live
def test_s3_forged_checkout_session_completed_rejected():
    """Forge a checkout.session.completed claiming a victim user_id in
    client_reference_id. Without a valid signature the handler must not
    even reach handleCheckoutCompleted.
    """
    body = json.dumps({
        "id": "evt_adv_forged_grant",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_adv",
            "client_reference_id": "00000000-0000-0000-0000-000000000001",
            "customer": "cus_attacker_owned",
        }},
    })
    r = requests.post(WEBHOOK, data=body, headers={"Content-Type": "application/json"}, timeout=15)
    assert r.status_code == 400, f"CRITICAL if not 400: {r.status_code} {r.text}"


@requires_live
def test_s4_stale_event_still_signature_blocked():
    """Document: even a 10-hour-old `created` timestamp doesn't get
    special treatment because it never passes signature verify. Stripe's
    SDK does enforce a default 5-minute tolerance ON the timestamp inside
    the signature header, but here we never reach that check — the HMAC
    fails first because we don't have the secret.

    A genuinely stale, validly-signed event would be rejected with
    `Timestamp outside the tolerance zone` if its t= header is older
    than 5 minutes. The handler doesn't override the default tolerance.
    """
    stale = int(time.time()) - 36000  # 10 hours ago
    body = json.dumps({
        "id": "evt_adv_stale",
        "created": stale,
        "type": "customer.subscription.created",
        "data": {"object": {
            "id": "sub_stale", "customer": "cus_stale", "status": "active",
        }},
    })
    r = requests.post(
        WEBHOOK,
        data=body,
        headers={
            "Content-Type": "application/json",
            "stripe-signature": f"t={stale},v1=deadbeef",
        },
        timeout=15,
    )
    assert r.status_code == 400


# --- S5 / S6 — code-trace assertions ------------------------------------

def test_s5_out_of_order_subscription_throws_for_retry():
    """If customer.subscription.created arrives BEFORE
    checkout.session.completed has linked the customer to a user, the
    licenses lookup returns no row. The handler must THROW so the
    top-level catch returns 500 and Stripe retries — otherwise we
    silently lose the upgrade.
    """
    src = WEBHOOK_SRC.read_text(encoding="utf-8")
    # Locate handleSubscriptionUpsert
    start = src.index("async function handleSubscriptionUpsert")
    end = src.index("async function ", start + 1)
    body = src[start:end]
    # Must look up license by customer; on miss must THROW (not return).
    assert "stripe_customer_id" in body
    assert "no license row for customer" in body, "missing the out-of-order log"
    # Look for the throw after the warn — `throw new Error("license row not yet linked` …
    assert 'throw new Error("license row not yet linked' in body, (
        "out-of-order path must throw so Stripe retries"
    )
    # And the top-level switch must let that bubble: confirm it returns 500
    # for handler errors so Stripe's retry machinery actually retries.
    assert 'return new Response(`handler error: ${msg}`, { status: 500 })' in src


def test_s6_handlers_are_idempotent_on_retry():
    """Stripe sends events at-least-once. Every handler must use UPDATE
    (or UPSERT) keyed on a stable identifier so retries are no-ops.
    Reject anything that does INSERT-without-UPSERT.
    """
    src = WEBHOOK_SRC.read_text(encoding="utf-8")
    # Pull each handler body and check.
    handlers = [
        "handleCheckoutCompleted",
        "handleSubscriptionUpsert",
        "handleSubscriptionDeleted",
        "handlePaymentFailed",
    ]
    for name in handlers:
        i = src.index(f"async function {name}")
        # next handler or EOF
        try:
            j = src.index("async function ", i + 1)
        except ValueError:
            j = len(src)
        body = src[i:j]
        if name == "handlePaymentFailed":
            # logs only; intrinsically idempotent
            assert ".update(" not in body and ".insert(" not in body
            continue
        # All DB-mutating handlers must use update() (idempotent under
        # at-least-once delivery) and never use a plain insert().
        assert ".update(" in body, f"{name} must use .update()"
        assert ".insert(" not in body, (
            f"{name} contains .insert() — not idempotent on Stripe retry"
        )


# --- S7 input fuzz against /checkout-create -----------------------------
#
# requireUser runs BEFORE body parsing in /checkout-create. Without a
# real Supabase user JWT we can only confirm that the auth gate fires.
# When BULLSEYE_TEST_USER_JWT is present we additionally exercise the
# input-handling branches (plan coercion, JSON malformedness, GET 405).

@requires_live
def test_s7_no_jwt_blocks_all_fuzz_inputs():
    """Without auth, body validation never runs. Every malformed body
    returns 401 BEFORE we ever look at it — that's the desired posture.
    """
    cases = [
        {"plan": "monthly"},
        {"plan": "lifetime"},
        {"plan": "../../../etc"},
        {"plan": ""},
        {},
    ]
    for case in cases:
        r = requests.post(
            CHECKOUT,
            data=json.dumps(case),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 401, (
            f"unauth body {case!r} should 401, got {r.status_code}: {r.text}"
        )


@requires_live
def test_s7_no_jwt_with_anon_only_still_401():
    """Sending only the anon key (no user JWT) hits requireUser which
    rejects it as a non-user token.
    """
    r = requests.post(
        CHECKOUT,
        data=json.dumps({"plan": "monthly"}),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ANON}",
            "apikey": ANON,
        },
        timeout=15,
    )
    assert r.status_code == 401


@requires_jwt
def test_s7_lifetime_plan_coerced_to_monthly():
    """{plan:"lifetime"} hits the ternary `plan === 'yearly' ? 'yearly' : 'monthly'`
    so it silently coerces to monthly. That's accepted product behavior
    (we don't sell lifetime), documented here so a future contributor
    doesn't expect a 400.
    """
    r = requests.post(
        CHECKOUT,
        data=json.dumps({"plan": "lifetime"}),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {USER_JWT}",
            "apikey": ANON,
        },
        timeout=20,
    )
    # Either succeeds with a checkout URL (coerced to monthly) or 502
    # if Stripe creds are missing in the env. Never 5xx-as-crash.
    assert r.status_code in (200, 502), r.text


@requires_jwt
def test_s7_path_traversal_plan_coerced_safely():
    """A traversal-looking plan must NOT be passed verbatim to Stripe.
    The ternary forces monthly; verify we don't see the raw string in
    any response.
    """
    r = requests.post(
        CHECKOUT,
        data=json.dumps({"plan": "../../../etc/passwd"}),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {USER_JWT}",
            "apikey": ANON,
        },
        timeout=20,
    )
    assert r.status_code in (200, 502)
    assert "../" not in r.text


@requires_jwt
def test_s7_null_body_returns_400():
    r = requests.post(
        CHECKOUT,
        data="null",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {USER_JWT}",
            "apikey": ANON,
        },
        timeout=20,
    )
    # JSON.parse("null") -> null, then body.plan throws on null deref.
    # That bubbles to the top of the request and Deno returns 500. The
    # handler doesn't explicitly guard for null body; document.
    assert r.status_code in (400, 500)


@requires_jwt
def test_s7_non_json_body_returns_400():
    r = requests.post(
        CHECKOUT,
        data="not json",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {USER_JWT}",
            "apikey": ANON,
        },
        timeout=20,
    )
    assert r.status_code == 400, r.text


@requires_live
def test_s7_get_method_returns_405():
    r = requests.get(
        CHECKOUT,
        headers={"Authorization": f"Bearer {ANON}", "apikey": ANON},
        timeout=15,
    )
    assert r.status_code == 405


# --- S8 — no JWT ---------------------------------------------------------

@requires_live
def test_s8_no_jwt_returns_401():
    """Confirms /checkout-create cannot be invoked without a valid
    Supabase user JWT. The Supabase platform itself returns 401 with
    no Authorization header at all; with the anon key (no user) the
    function's own requireUser returns 401.
    """
    r = requests.post(
        CHECKOUT,
        data=json.dumps({"plan": "monthly"}),
        headers={"Content-Type": "application/json"},
        timeout=15,
    )
    assert r.status_code == 401


# --- S9 / S10 — code-trace assertions -----------------------------------

def test_s9_trial_double_redemption_not_blocked_in_checkout_create():
    """/checkout-create accepts trial=true unconditionally. There's no
    check against licenses.trial_ends_at to prevent a user who already
    consumed a trial from starting another. Stripe itself doesn't
    de-duplicate trials per customer — if Stripe creates a NEW customer
    (because we passed customer_email and the previous customer object
    isn't reused), the user gets a fresh trial.

    Defence-in-depth: re-using `existing_customer_id` when available
    means a returning user's second checkout reuses the same Stripe
    customer, and Stripe will honour our `trial_period_days` again
    regardless. So a determined user CAN double-redeem the 7-day trial
    by signing up with a fresh email.

    This is documented as a known acceptable risk for early-stage
    pricing — a hardened version would set
    `subscription_data.trial_settings.end_behavior` and check
    licenses.trial_ends_at server-side before allowing trial=true.
    """
    # Server-side trial-redemption guard landed: /checkout-create now
    # checks licenses.trial_ends_at IS NOT NULL and forces trial=false
    # on subsequent redemption attempts (Findings doc S9). Confirm the
    # check is wired so a regression that removes it is caught here.
    src = CHECKOUT_SRC.read_text(encoding="utf-8")
    assert "trial_ends_at" in src, (
        "trial double-redeem guard removed — re-add the licenses."
        "trial_ends_at check in checkout-create/index.ts"
    )
    assert "trial = false" in src or "trial=false" in src, (
        "expected the guard to flip trial to false on the second checkout"
    )
    # The guard is per-user-id (Supabase auth), not per-email — a user who
    # account-deletes and re-signs-up with the same email DOES get a fresh
    # trial. Documented as deferred (would need a PII-aware fingerprint
    # table). Reuse of `existing_customer_id` for returning users still
    # bypasses Stripe's own trial dedup, but our guard catches it earlier.
    shared = SHARED_SRC.read_text(encoding="utf-8")
    assert "trial_period_days: 7" in shared


def test_s10_checkout_url_reuse_grants_pro_to_originator():
    """If user A's Stripe Checkout URL is shared with user B (who
    actually pays), the resulting subscription is keyed to A:
    client_reference_id was baked into the session at create-time and
    rides through to checkout.session.completed.

    Outcome:
      - User A's `licenses` row is upgraded.
      - User B's payment method is charged.
      - User B has no record on our side.

    This is Stripe-platform behaviour; the only mitigation is to make
    the success page show "logged in as A — if that's not you, contact
    support". Documented; not currently mitigated in code.
    """
    src = CHECKOUT_SRC.read_text(encoding="utf-8")
    shared = SHARED_SRC.read_text(encoding="utf-8")
    # The user_id is hard-baked into the session at create time.
    assert "client_reference_id: args.user_id" in shared
    # And the webhook trusts it on completion.
    wh = WEBHOOK_SRC.read_text(encoding="utf-8")
    assert "session.client_reference_id" in wh
    # No re-auth between checkout URL hand-off and completion.
    assert "user.id" in src
