"""Scoring formula + condition signals + (free-tier-only) LLM checks.

PHASE 2 PORT: copy from `../../../../../deal_finder/src/deal_finder/appraisal/`:
    - formula.py            (compute_score, percentile rank, confidence)
    - condition_signals.py  (regex-based "like new" / "for parts" flags)
    - normalizer.py         (title cleanup for comp searches)

OMIT (paid-tier only or post-launch):
    - secondary_check.py    (LLM verification — paid only, defer to v1.1)
    - minimax_client.py     (cloud LLM — defer)
    - worker.py             (safety-drain backup pass — defer)

Score logic stays CLIENT-SIDE per blueprint locked decisions. Means a
formula bug requires an app update to fix; auto-update mechanism in
Phase 3 covers that.
"""
