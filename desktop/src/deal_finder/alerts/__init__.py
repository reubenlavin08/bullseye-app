"""Alert delivery — desktop toasts + cloud-sent emails.

PHASE 2 REWRITE: the personal tool sends emails directly via SMTP. The
product sends them via the cloud `/alerts-send` Edge Function (which
uses Resend). This module is the orchestration layer:

    - free tier: 1 daily digest at 8am local
    - paid tier: instant emails with 60s batching hold

Source files to look at when porting:
    - ../../../../../deal_finder/src/deal_finder/alerts/digest.py
        - keep: collect_pending_for_email, _passes_watch_bounds,
                mark_notified, batching hold logic
        - replace: smtp send -> cloud.alerts.send_digest/send_instant
    - ../../../../../deal_finder/src/deal_finder/alerts/email.py  (DELETE — cloud handles SMTP)

Desktop toasts fire on EVERY match regardless of tier — that's the
free-and-immediate path. Email is the tier differentiator.
"""
