"""License + tier fetcher.

Returns a LicenseInfo dict:
    {
        tier: 'free' | 'paid' | 'trial',
        watches_limit: int,            # 3 for free, None for paid
        poll_interval_min: int,        # 30 for free, 5 for paid
        expires_at: ISO timestamp | None,
        trial_ends_at: ISO timestamp | None,
        min_supported_version: str,    # KILL SWITCH — if our version <
                                       #   this, app shows "please update"
    }

The `min_supported_version` field lets us EOL old client builds
without a forced update — anyone running an older version gets a
hard-stop banner. Set this from Supabase dashboard when we ship a
breaking change.
"""
from __future__ import annotations


def fetch_license() -> dict:
    """Hit `/license`. Returns LicenseInfo. Raises CloudUnavailable
    on transport failure (caller usually has a cached value to fall
    back to)."""
    # TODO: client.post('license', {})
    raise NotImplementedError
