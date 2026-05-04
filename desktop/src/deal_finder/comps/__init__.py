"""Comp data — thin wrapper around `cloud/comps.py`.

PHASE 2 REWRITE: the personal tool has rich logic here (eBay primary,
Marketplace fallback, parts/accessory exclusion, normalization
factor). All of that moves to the cloud function. Locally we just:

    1. Call `cloud.comps.get_comps(search_term, region)`
    2. Apply the asking-vs-sold discount factor (0.85) if needed
       (cloud could do this but keeping fair-value math client-side
       lets us tune without redeploying the function)
    3. Return CompStats-shaped dict to the appraisal pipeline

OMIT entirely from the desktop app:
    - ebay.py     (cloud handles)
    - marketplace.py asking-price comps (cloud handles, or we drop
                    that fallback path entirely — TBD in Phase 2)
"""
