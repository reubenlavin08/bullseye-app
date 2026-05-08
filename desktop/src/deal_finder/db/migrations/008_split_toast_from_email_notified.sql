-- 008: split desktop-toast tracking from email-notification tracking.
--
-- Background. Before this migration there was a single `notified`
-- column on `listings` that meant "user has been told about this
-- listing." When the email digest pipeline (alerts/digest.py +
-- /alerts-send Edge Function) was the only consumer, that meaning was
-- well-defined: notified=1 ⇔ "an email went out." But on 2026-05-07
-- the desktop-toast path in scheduler/jobs.py:_process_new_listing
-- was also wired up to use the same column — toast fires, then
-- UPDATE listings SET notified=1. The intent there was to dedupe
-- toast firings on re-poll, which works for that purpose.
--
-- The collision: when both the toast and the email pipeline are
-- active, the toast (which fires synchronously inside _process_new_listing
-- right after scoring) marks notified=1 BEFORE the email digest job
-- runs (every 15s). The digest job's collector queries
-- `notified=0 AND deal_score>=threshold`, finds nothing, and no email
-- ever goes out.
--
-- Fix. Add a separate `toast_fired` column owned by the desktop-toast
-- path. The original `notified` column reverts to its original
-- meaning: "an email went out via /alerts-send." This keeps the toast
-- dedup behavior intact while letting the email pipeline see new
-- high-score listings.
--
-- This is additive only (no DROP, no rename); existing rows pick up
-- the default 0 via SQLite's ADD COLUMN semantics, which means
-- previously-toast-fired listings will look like "toast not yet
-- fired" to the new code. Acceptable trade-off — at worst a re-poll
-- of an old listing fires a duplicate toast once, after which the new
-- column gets set and dedup resumes.
--
-- Email backfill: listings marked notified=1 by the toast path but
-- never actually emailed are stranded — they look "already emailed"
-- to the digest pipeline. Luckily the two paths leave distinguishable
-- footprints:
--
--   - email digest (alerts/digest.py::_mark_notified) sets BOTH
--     notified=1 AND notified_at=CURRENT_TIMESTAMP.
--   - toast (the old code in scheduler/jobs.py::_process_new_listing
--     pre-2026-05-07-fix) set ONLY notified=1, leaving notified_at
--     NULL because the UPDATE didn't touch that column.
--
-- So `notified=1 AND notified_at IS NULL` is a clean indicator of
-- "toast claimed this listing but no email ever went out." Reset
-- those back to notified=0 so the email pipeline picks them up on
-- its next 15s tick. The toast itself doesn't re-fire because
-- toast_fired (the new column) doesn't get the same backfill — at
-- worst a re-poll of an old listing fires one duplicate toast, after
-- which dedup resumes via the new column.
--
-- This is a one-shot backfill that runs once per install (the
-- migration runner records 008 as applied and never re-runs it).

ALTER TABLE listings ADD COLUMN toast_fired INTEGER DEFAULT 0;

UPDATE listings
   SET notified = 0
 WHERE notified = 1
   AND notified_at IS NULL
   AND deal_score IS NOT NULL
   AND deal_score >= 70;

-- An index on toast_fired isn't worth it — the toast path looks up
-- a single row by id, not a scan.
