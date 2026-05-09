/* Home tab — pulls a couple of cheap endpoints and stamps numbers
   into stat cards. Re-uses /api/dashboard/summary so we don't need a
   new server route. */
(function () {
    "use strict";
    var b = window.bullseye;

    // 5s cache for /api/dashboard/appraisal-feed?filter=passed shared
    // by loadRecent (slices to 8) and loadHotDeal (uses all 20). The
    // in-flight promise dedup matters: both callers run from the same
    // visibility-restore burst and would race the cold-cache fetch
    // without it.
    var _passedFeedCache = null;
    var _passedFeedAt = 0;
    var _passedFeedInflight = null;
    var _PASSED_FEED_TTL_MS = 5000;
    async function getPassedFeedItems() {
        var now = Date.now();
        if (_passedFeedCache != null && (now - _passedFeedAt) < _PASSED_FEED_TTL_MS) {
            return _passedFeedCache;
        }
        if (_passedFeedInflight) return _passedFeedInflight;
        _passedFeedInflight = (async function () {
            try {
                var s = await b.apiGet("/api/dashboard/appraisal-feed?limit=20&filter=passed");
                var items = (s && (s.listings || s.items || s.rows)) || [];
                _passedFeedCache = items;
                _passedFeedAt = Date.now();
                return items;
            } finally {
                _passedFeedInflight = null;
            }
        })();
        return _passedFeedInflight;
    }

    function fmtCountdown(secs) {
        if (secs === null || secs === undefined) return "—";
        if (secs < 0) secs = 0;
        if (secs < 60) return secs + "s";
        var m = Math.floor(secs / 60);
        var s = Math.floor(secs % 60);
        return m + "m " + (s < 10 ? "0" : "") + s + "s";
    }

    async function loadStats() {
        try {
            var s = await b.apiGet("/api/dashboard/summary");
            var nWatches = s.active_watches || 0;
            // Show or hide the first-run onboarding CTA based on
            // active-watch count. Empty home = unknown what to do
            // next; this card removes that ambiguity.
            var onboarding = document.getElementById("onboarding-cta-section");
            if (onboarding) {
                onboarding.hidden = nWatches > 0;
            }
            // The "Start your searches now" recovery button only makes
            // sense when there's something to poll — hide it when the
            // user hasn't created their first watch yet (the onboarding
            // CTA owns that empty-state experience).
            var pollNowCell = document.getElementById("home-poll-now-cell");
            if (pollNowCell) {
                pollNowCell.hidden = nWatches === 0;
            }
            document.getElementById("stat-watches-active").textContent =
                nWatches != null ? nWatches : "—";
            var f = s.funnel_today || {};
            document.getElementById("stat-deals").textContent =
                f.appraised != null ? f.appraised : "—";
            document.getElementById("stat-alerts").textContent =
                (s.rates && s.rates.emails_today != null) ? s.rates.emails_today : "—";

            var pt = s.poll_timer || {};
            var secs = pt.seconds_until_next;
            if (secs == null && pt.next_poll_iso) {
                var dt = Date.parse(pt.next_poll_iso);
                if (!isNaN(dt)) secs = Math.round((dt - Date.now()) / 1000);
            }
            document.getElementById("stat-next-poll").textContent = fmtCountdown(secs);
        } catch (e) {
            // /api/dashboard/summary is auth-only — silently degrade.
        }
    }

    // Pull /api/watches once to get the measured global average poll
    // cadence (24h). This is the "honest" advertising number — what
    // users actually observe, not the theoretical license floor. Hides
    // the stat cell entirely if there isn't enough data yet (a fresh
    // install needs a few polls before the average means anything).
    async function loadCadence() {
        try {
            var w = await b.apiGet("/api/watches");
            var cell = document.getElementById("stat-cell-cadence");
            var num = document.getElementById("stat-avg-cadence");
            if (!cell || !num) return;
            var s = w && w.global_avg_poll_gap_s;
            if (s == null || s <= 0) {
                // Not enough polls in the last 24h to compute. Hide the
                // cell rather than showing "—" forever.
                cell.style.display = "none";
                return;
            }
            cell.style.display = "";
            var fmt = (b && b.fmtDurationShort) || function (x) { return Math.round(x) + "s"; };
            num.textContent = fmt(s);
        } catch (e) {
            // /api/watches is auth-only — silently degrade.
        }
    }

    async function loadStreak() {
        try {
            var s = await b.apiGet("/api/streak");
            // Cloud Edge Function returns `current_streak`, not
            // `current_streak_days` — accept both for forward-compat.
            var days = s.current_streak;
            if (days == null) days = s.current_streak_days;
            var current = days != null ? days : 0;
            document.getElementById("streak-days").textContent = current;

            var nextEl = document.getElementById("streak-next");
            if (s.pro_days_banked != null) {
                nextEl.textContent = "· " + s.pro_days_banked + " Pro day(s) banked";
            } else {
                nextEl.textContent = "";
            }

            /* Progress bar to the next milestone. Cloud awards Pro
               days at 3/7/14/30-day streaks. We always show the
               progress toward the NEXT one, so the bar fills visibly
               every single day. */
            var progressEl = document.getElementById("streak-progress");
            var fillEl = document.getElementById("streak-progress-fill");
            var captionEl = document.getElementById("streak-progress-caption");
            if (progressEl && fillEl && captionEl) {
                var milestones = [3, 7, 14, 30];
                var next = null;
                for (var i = 0; i < milestones.length; i++) {
                    if (current < milestones[i]) { next = milestones[i]; break; }
                }
                if (next == null) {
                    progressEl.hidden = true;
                } else {
                    var pct = Math.min(100, Math.round((current / next) * 100));
                    fillEl.style.width = pct + "%";
                    var daysLeft = next - current;
                    var rewardLabel = next === 3 ? "first reward"
                                    : next === 7 ? "free week of Pro"
                                    : next === 14 ? "bigger reward"
                                    : "30-day milestone";
                    captionEl.textContent = current + " / " + next +
                        " — " + daysLeft + " day" + (daysLeft === 1 ? "" : "s") +
                        " to " + rewardLabel;
                    progressEl.hidden = false;
                }
            }
        } catch (e) {
            // streak unavailable — hide the row, never crash the page
            var card = document.getElementById("streak-days");
            if (card) card.textContent = "—";
            var p = document.getElementById("streak-progress");
            if (p) p.hidden = true;
        }
    }

    // Recent THRESHOLD-PASSING finds — what your watches actually
    // delivered (not the raw scored feed). The user pointed out
    // 2026-05-07 that there was no obvious "here are your search
    // results" surface on Home — Insights / Recent finds / Activity
    // were all there but scattered. This is now THE answer-seeking
    // surface: the most recent listings that hit each watch's
    // threshold, newest first. Click → opens the listing.
    //
    // filter=passed (server-side) means score >= threshold AND not
    // rejected. Fresh installs / quiet days show a friendly empty state.
    // Endpoint key is `listings` (corrected last week — same bug class
    // as tab_activity.js had) but we accept items/rows for safety.
    async function loadRecent() {
        var feed = document.getElementById("recent-activity");
        if (!feed) return;
        try {
            var items = (await getPassedFeedItems()).slice(0, 8);
            if (!items.length) {
                feed.innerHTML =
                    '<div class="muted" style="padding:10px 0;">' +
                        "No threshold hits yet. " +
                        "Listings show up here as your watches find " +
                        "deals scoring above their alert threshold." +
                    "</div>";
                return;
            }
            feed.innerHTML = items.slice(0, 8).map(function (it) {
                var score = it.deal_score;
                var scoreCls = b.scoreClass(score);
                var url = b.safeUrl(it.listing_url);
                var title = b.escapeHTML(it.title || "(untitled)");
                var rel = b.fmtRelative(it.scraped_at);
                var price = b.fmtMoney(it.price);
                var savingsLine = "";
                if (
                    typeof it.fair_value === "number" &&
                    typeof it.price === "number" &&
                    it.fair_value > it.price
                ) {
                    savingsLine =
                        " &middot; <span style=\"color:var(--good);\">save " +
                        b.fmtMoney(it.fair_value - it.price) +
                        "</span>";
                }
                // The score badge is now a button that opens the
                // breakdown modal — answers the user-reported gap
                // "nowhere on my listings can I click to see the
                // score breakdown and see their comps". The title
                // stays a Marketplace link so the existing "open
                // listing" muscle memory still works.
                var lid = b.escapeHTML(String(it.id || ""));
                return '<div class="activity-row">'
                    + '<button type="button" class="activity-score ' + scoreCls
                    + ' activity-score-btn" data-listing-id="' + lid
                    + '" title="Click for score breakdown + eBay comps">'
                    + b.fmtScore(score) + '</button>'
                    + '<div class="activity-title"><a href="' + b.escapeHTML(url) + '" target="_blank" rel="noopener">' + title + '</a>'
                    + '<div class="muted" style="font-size:11px;">' + price + ' &middot; ' + b.escapeHTML(it.keyword || "") + savingsLine + '</div>'
                    + '<button type="button" class="activity-bd-link" data-listing-id="' + lid + '">'
                    +   'Score breakdown <span aria-hidden="true">&rarr;</span>'
                    + '</button>'
                    + '</div>'
                    + '<div class="activity-meta">' + rel + '</div>'
                    + '</div>';
            }).join("");
            // Wire BOTH affordances (score badge + explicit breakdown
            // link) to the same modal opener.
            feed.querySelectorAll(".activity-score-btn, .activity-bd-link").forEach(function (btn) {
                btn.addEventListener("click", function (ev) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    var lid = btn.getAttribute("data-listing-id");
                    if (lid) openBreakdownModal(lid);
                });
            });
        } catch (e) {
            feed.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
        }
    }

    /* ------------------------------------------------------------ */
    /* Hot-deal banner.                                              */
    /*                                                                */
    /* Polls the same /api/dashboard/appraisal-feed?filter=passed    */
    /* the recent-activity feed already loads, so it's free          */
    /* (browser caches the GET). Picks the newest listing where      */
    /* deal_score >= HOT_DEAL_MIN_SCORE AND scraped within the last  */
    /* HOT_DEAL_MAX_AGE_HOURS, that the user hasn't dismissed yet.   */
    /*                                                                */
    /* Dismissal is per-listing via localStorage — once you click X, */
    /* that specific id never re-shows, but the NEXT hot deal will   */
    /* still trigger the banner. Single-key store keeps the storage  */
    /* footprint trivial; we only remember the last dismissed id     */
    /* because hot deals come in sequence, not in parallel.          */
    /* ------------------------------------------------------------ */
    var HOT_DEAL_MIN_SCORE = 85;
    var HOT_DEAL_MAX_AGE_HOURS = 24;
    var HOT_DEAL_LS_KEY = "bullseye_hotdeal_dismissed_id";

    async function loadHotDeal() {
        var card = document.getElementById("hot-deal-card");
        if (!card) return;
        var banner = card;  // backwards-compat alias for old refs below
        var dismissed = "";
        try { dismissed = localStorage.getItem(HOT_DEAL_LS_KEY) || ""; }
        catch (e) { /* localStorage unavailable — proceed without dedup */ }
        try {
            var items = await getPassedFeedItems();
            var cutoff = Date.now() - HOT_DEAL_MAX_AGE_HOURS * 3600 * 1000;
            var hot = null;
            for (var i = 0; i < items.length; i++) {
                var it = items[i];
                if (!it || it.deal_score == null) continue;
                if (Number(it.deal_score) < HOT_DEAL_MIN_SCORE) continue;
                if (String(it.id) === dismissed) continue;
                var ts = it.scraped_at ? Date.parse(it.scraped_at) : 0;
                if (!ts || ts < cutoff) continue;
                hot = it;
                break;
            }
            if (!hot) {
                banner.hidden = true;
                return;
            }
            // Wire the new vertical-card layout. IDs are:
            //   #hot-deal-title    title (clamped 3 lines)
            //   #hot-deal-stats    score / price / save line (HTML)
            //   #hot-deal-keyword  watch keyword (muted)
            //   #hot-deal-cta      "View on Marketplace"
            //   #hot-deal-breakdown "See score breakdown"
            //   #hot-deal-dismiss  corner X
            // Plus: title/kicker/stats/keyword are clickable to
            // navigate to /activity?focus=<id> (per user 2026-05-07:
            // "when I click on Hot Deal, it should take me to the
            // insights page... where I can see the score breakdown
            // of it and the other top listings as well").
            var titleEl = document.getElementById("hot-deal-title");
            var statsEl = document.getElementById("hot-deal-stats");
            var keywordEl = document.getElementById("hot-deal-keyword");
            var ctaEl = document.getElementById("hot-deal-cta");
            var bdEl = document.getElementById("hot-deal-breakdown");
            var dismissEl = document.getElementById("hot-deal-dismiss");
            var kickerEl = card.querySelector(".hot-deal-kicker");

            titleEl.textContent = hot.title || "(untitled listing)";

            // Stats line: "Score 92 · $185 · save $134". Built as
            // innerHTML so we can color the score and savings spans
            // independently with classes the CSS uses.
            var statsHtml = '<span class="hot-deal-score">Score '
                + Math.round(hot.deal_score) + '</span> · '
                + b.fmtMoney(hot.price);
            if (
                typeof hot.fair_value === "number"
                && typeof hot.price === "number"
                && hot.fair_value > hot.price
            ) {
                statsHtml += ' · <span class="hot-deal-savings">save '
                    + b.fmtMoney(hot.fair_value - hot.price) + '</span>';
            }
            statsEl.innerHTML = statsHtml;
            keywordEl.textContent = hot.keyword || "";

            // Primary CTA — opens FB listing in the system browser
            // via /api/open-external. Falls back to window.open if
            // that endpoint hiccups.
            ctaEl.onclick = function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                var url = hot.listing_url || "";
                if (!url) return;
                b.apiPost("/api/open-external", { url: url }).catch(function () {
                    window.open(url, "_blank", "noopener");
                });
            };

            // Secondary link — opens the breakdown modal in-place
            // (doesn't navigate away from Home).
            bdEl.onclick = function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                openBreakdownModal(String(hot.id));
            };

            // Click the body areas (kicker / title / stats / keyword)
            // to navigate to /activity?focus=<id>, where the listing
            // is scrolled into view and every score is clickable for
            // its own breakdown — exactly what the user asked for.
            // The CTA + breakdown buttons stopPropagation so they
            // don't also trigger this navigation.
            function navigateToActivity() {
                window.location.href = "/activity?focus="
                    + encodeURIComponent(String(hot.id));
            }
            [kickerEl, titleEl, statsEl, keywordEl].forEach(function (el) {
                if (!el) return;
                el.style.cursor = "pointer";
                el.onclick = function (ev) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    navigateToActivity();
                };
            });

            dismissEl.onclick = function (ev) {
                ev.stopPropagation();
                try { localStorage.setItem(HOT_DEAL_LS_KEY, String(hot.id)); }
                catch (e) { /* ignore quota errors */ }
                card.hidden = true;
            };

            card.hidden = false;
        } catch (e) {
            // Endpoint is auth-only; quietly hide the banner if we
            // can't load (mirrors loadStats's silent-degrade pattern).
            banner.hidden = true;
        }
    }

    /* Score-breakdown modal — thin wrapper around the global
       implementation in shell.js. Was originally inlined here but
       moved up to the shell on 2026-05-07 so /activity can use the
       same modal (per user feedback "click on Hot Deal should take
       me to a page where I can see the score breakdown of the
       other top listings as well"). */
    function openBreakdownModal(listingId) {
        return b.openBreakdownModal(listingId);
    }

    function wireRedeem() {
        var btn = document.getElementById("banked-redeem-btn");
        if (!btn) return;
        btn.addEventListener("click", async function () {
            btn.disabled = true;
            btn.textContent = "Redeeming…";
            try {
                await b.apiPost("/api/streak/redeem", {});
                b.toast("Trial activated. Reloading…", "success");
                setTimeout(function () { window.location.reload(); }, 800);
            } catch (e) {
                btn.disabled = false;
                btn.textContent = "Try again";
                b.toast(b.describeError(e), "error");
            }
        });
    }

    /* ---------- mini-heatmap (last 14 days) -------------------------
       Wispr Flow's Home shows a compact activity strip; we do the same
       so the user gets a quick "am I active this week" read without
       leaving Home. Clicking through goes to the full Insights view.
    ----------------------------------------------------------------- */

    function bucket(n) {
        if (!n || n <= 0) return 0;
        if (n === 1) return 1;
        if (n <= 3) return 2;
        if (n <= 6) return 3;
        return 4;
    }

    function buildMiniHeatmapSvg(days) {
        var CELL = 14, GAP = 4;
        var W = days.length * (CELL + GAP);
        var H = CELL;
        var parts = ['<svg class="mini-heatmap-svg" viewBox="0 0 ' + W + ' ' + H
            + '" width="' + W + '" height="' + H + '" role="img" aria-label="last 14 days">'];
        days.forEach(function (cell, i) {
            var x = i * (CELL + GAP);
            var lvl = bucket(cell.deals);
            var label = cell.date + ": " + cell.deals
                + (cell.deals === 1 ? " deal" : " deals");
            parts.push(
                '<rect class="hm-cell hm-l' + lvl + '" '
                + 'x="' + x + '" y="0" width="' + CELL + '" height="' + CELL + '" '
                + 'rx="3" ry="3"><title>' + b.escapeHTML(label) + '</title></rect>'
            );
        });
        parts.push('</svg>');
        return parts.join("");
    }

    function renderTodayTop(top) {
        // D4 Hub polish: empty state is silent — the entire section
        // collapses when there's no scoring deal yet, instead of
        // rendering a "no deals today" placeholder card. Quieter UI on
        // slow days; the heatmap below still gives momentum context.
        var section = document.getElementById("daily-goal-section");
        var card = document.getElementById("daily-goal-card");
        if (!section || !card) return;
        if (!top) {
            section.hidden = true;
            return;
        }
        section.hidden = false;
        var photoEl = document.getElementById("daily-goal-photo");
        if (top.photo_url) {
            photoEl.innerHTML = '<img src="' + b.escapeHTML(top.photo_url) + '" alt="" loading="lazy">';
        } else {
            photoEl.innerHTML = '<div class="photo-empty">—</div>';
        }
        var titleEl = document.getElementById("daily-goal-title");
        var metaEl = document.getElementById("daily-goal-meta");
        var scoreEl = document.getElementById("daily-goal-score");
        var url = b.safeUrl(top.listing_url);
        titleEl.innerHTML = '<a href="' + b.escapeHTML(url)
            + '" target="_blank" rel="noopener">' + b.escapeHTML(top.title || "(untitled)") + '</a>';
        var price = b.fmtMoney(top.price);
        metaEl.textContent = price === "—" ? "" : price;
        scoreEl.textContent = b.fmtScore(top.deal_score);
        scoreEl.className = "daily-goal-score " + b.scoreClass(top.deal_score);
    }

    async function loadHomeInsights() {
        var hmEl = document.getElementById("home-mini-heatmap");
        try {
            var res = await b.apiGet("/api/insights/heatmap?days=14");
            renderTodayTop(res.today_top);
            if (hmEl) {
                hmEl.innerHTML = buildMiniHeatmapSvg(res.days || []);
            }
        } catch (e) {
            if (hmEl) {
                hmEl.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
            }
        }
    }

    /* ---------- top saved-searches preview --------------------------
       Pulls /api/dashboard/per-watch (already used by /stats) and
       renders the top 3 by hits_24h as clickable mini-cards. Empty
       state suggests creating a watch. */

    async function loadTopWatches() {
        var el = document.getElementById("home-top-watches");
        if (!el) return;
        try {
            var res = await b.apiGet("/api/dashboard/per-watch");
            var rows = (res && (res.rows || res.watches)) || [];
            if (!rows.length) {
                el.innerHTML =
                    '<div class="card top-watches-empty">' +
                    '<div>No saved searches yet.</div>' +
                    '<a href="/watches" class="btn btn-primary" style="margin-top:10px;">Create your first search</a>' +
                    '</div>';
                return;
            }
            // Sort by hits_24h DESC, fallback polls_24h.
            rows.sort(function (a, b) {
                var ha = a.hits_24h || 0, hb = b.hits_24h || 0;
                if (ha !== hb) return hb - ha;
                return (b.polls_24h || 0) - (a.polls_24h || 0);
            });
            var top = rows.slice(0, 3);
            el.innerHTML =
                '<div class="top-watches-grid">' +
                top.map(function (r) {
                    var hits = r.hits_24h || 0;
                    var hitsCls = hits > 0 ? "good" : "muted";
                    var last = r.last_scrape_iso ? b.fmtRelative(r.last_scrape_iso) : "no polls yet";
                    return '<a class="top-watch-card" href="/watches">' +
                        '<div class="top-watch-keyword">' + b.escapeHTML(r.keyword || "—") + '</div>' +
                        '<div class="top-watch-stats">' +
                            '<span class="' + hitsCls + '"><strong>' + hits + '</strong> deal' + (hits === 1 ? '' : 's') + ' today</span>' +
                            '<span class="muted">· ' + b.escapeHTML(last) + '</span>' +
                        '</div>' +
                    '</a>';
                }).join("") +
                '</div>';
        } catch (e) {
            el.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
        }
    }

    /* ---------- lifetime savings flex (Tier 1 retention) -----------
       Pulls /api/insights/lifetime and renders the "$X saved across N
       deals + That's enough to buy a ..." card on Home. Section stays
       hidden until the user has at least 1 scored deal — empty cards
       feel sad and aren't a flex. */

    async function loadSavingsFlex() {
        var section = document.getElementById("home-savings-section");
        if (!section) return;
        try {
            var res = await b.apiGet("/api/insights/lifetime");
            var deals = res.deals_total || 0;
            var savings = res.savings_total || 0;
            if (deals < 1 || savings <= 0) {
                section.hidden = true;
                return;
            }
            section.hidden = false;
            document.getElementById("home-savings-amount").textContent =
                b.fmtMoney(savings);
            document.getElementById("home-savings-count").textContent = deals;
            document.getElementById("home-savings-deals-word").textContent =
                deals === 1 ? "deal" : "deals";
            var flexEl = document.getElementById("home-savings-flex");
            // Build the "next milestone" hint. Retuned 2026-05-07 along
            // with the achievement-budget retune in
            // cloud/supabase/functions/_shared/achievements.ts — most
            // savings milestones now unlock UI INSIGHTS (favorite term,
            // hunt rhythm chart, etc.) rather than Pro days. Only the
            // $5k milestone keeps a real Pro-day reward. The "reward"
            // text below names the unlock so the user sees what they're
            // working toward, not a generic "more savings = more days"
            // claim that the system no longer pays out.
            var milestones = [
                { amount: 500,    reward: "Favorite Search Term insight"             },
                { amount: 2500,   reward: "Hunt Rhythm chart"                        },
                { amount: 10000,  reward: "1 Pro day + Hottest Category insight"     },
                { amount: 50000,  reward: "1 Pro day + Savings Velocity chart"       },
                { amount: 100000, reward: "1 Pro day (final savings milestone)"      },
            ];
            var nextMilestone = null;
            for (var i = 0; i < milestones.length; i++) {
                if (savings < milestones[i].amount) {
                    nextMilestone = milestones[i];
                    break;
                }
            }
            var ladderHint = "";
            if (nextMilestone) {
                var remaining = nextMilestone.amount - savings;
                ladderHint =
                    "Save " + b.fmtMoney(remaining) +
                    " more (" + b.fmtMoney(nextMilestone.amount) +
                    " milestone) to unlock " + nextMilestone.reward + ".";
            } else {
                ladderHint =
                    "You've cleared every savings milestone. The lifetime savings chart is unlocked on the Insights tab.";
            }
            // Combine cultural-flex (existing) with the rewards-ladder
            // hint (new). Cultural-flex shows whenever the cloud
            // returned one; the ladder hint shows always (more useful).
            var combined = ladderHint;
            if (res.cultural_flex) {
                combined =
                    "That's enough to buy " + res.cultural_flex + ". " +
                    ladderHint;
            }
            flexEl.textContent = combined;
            flexEl.hidden = false;
        } catch (e) {
            section.hidden = true;
        }
    }

    /* ---------- achievement gallery -------------------------------
       Modal opener + renderer. Modal HTML lives in app_shell.html.
       Pulls /api/achievements (proxy to cloud) and renders a grid of
       locked/unlocked badges grouped by family. */

    async function loadAchievementsModal() {
        var grid = document.getElementById("ach-grid");
        if (!grid) return;
        grid.innerHTML = '<div class="muted" style="padding:24px;text-align:center;">loading…</div>';
        try {
            var res = await b.apiGet("/api/achievements");
            if (!res || !res.ok) {
                grid.innerHTML = '<div class="muted" style="padding:24px;text-align:center;">Achievements unavailable. Try again later.</div>';
                return;
            }
            document.getElementById("ach-banked").textContent =
                res.pro_days_banked != null ? res.pro_days_banked : "—";
            document.getElementById("ach-unlocked").textContent =
                (res.unlocked_count != null ? res.unlocked_count : "—") +
                " / " + (res.total_count || "—");
            document.getElementById("ach-lifetime").textContent =
                res.pro_days_lifetime != null ? res.pro_days_lifetime : "—";

            // Group by family for visual section headers.
            var families = {};
            (res.achievements || []).forEach(function (a) {
                if (!families[a.family]) families[a.family] = [];
                families[a.family].push(a);
            });
            var FAMILY_ORDER = ["streak", "deals", "savings", "social", "engagement"];
            var FAMILY_LABEL = {
                streak: "Daily streak",
                deals: "Big finds",
                savings: "Lifetime savings",
                social: "Refer & earn",
                engagement: "Getting started",
            };
            var html = "";
            FAMILY_ORDER.forEach(function (fam) {
                var items = families[fam];
                if (!items || !items.length) return;
                html += '<div class="ach-family">' +
                    '<div class="ach-family-head">' + FAMILY_LABEL[fam] + '</div>' +
                    '<div class="ach-family-grid">' +
                    items.map(function (a) {
                        var unlocked = !!a.unlocked;
                        var cls = unlocked ? "ach-card ach-unlocked" : "ach-card ach-locked";
                        var rewardLine = a.pro_days > 0
                            ? '+' + a.pro_days + ' Pro day' + (a.pro_days === 1 ? '' : 's')
                            : '';
                        return '<div class="' + cls + '" title="' + b.escapeHTML(a.hint) + '">' +
                            '<div class="ach-icon">' + (unlocked ? a.icon : '🔒') + '</div>' +
                            '<div class="ach-body">' +
                                '<div class="ach-name">' + b.escapeHTML(a.name) + '</div>' +
                                '<div class="ach-desc muted">' +
                                    b.escapeHTML(unlocked ? a.description : a.hint) +
                                '</div>' +
                                (rewardLine ? '<div class="ach-reward">' + rewardLine + '</div>' : '') +
                            '</div>' +
                        '</div>';
                    }).join("") +
                    '</div></div>';
            });
            grid.innerHTML = html;
        } catch (e) {
            grid.innerHTML = '<div class="muted" style="padding:24px;text-align:center;">' + b.escapeHTML(b.describeError(e)) + '</div>';
        }
    }

    function wireAchievementsButton() {
        var btn = document.getElementById("open-achievements");
        var modal = document.getElementById("achievements-modal");
        if (!btn || !modal) return;
        btn.addEventListener("click", function () {
            modal.classList.add("is-open");
            loadAchievementsModal();
        });
    }

    // "Start your searches now" recovery button (added 2026-05-07).
    // POSTs to /api/watches/poll-now which kicks coordinator_tick()
    // for every active watch. Endpoint is rate-limited to one click per
    // 60s server-side, so a frantic user can't hammer Facebook.
    function wirePollNowButton() {
        var btn = document.getElementById("home-poll-now-btn");
        if (!btn) return;
        btn.addEventListener("click", async function () {
            if (btn.disabled) return;
            var originalText = btn.querySelector("span");
            var originalLabel = originalText ? originalText.textContent : "";
            btn.disabled = true;
            if (originalText) originalText.textContent = "Starting…";
            try {
                var r = await b.apiPost("/api/watches/poll-now", {});
                if (r && r.ok) {
                    if (originalText) {
                        originalText.textContent =
                            r.started > 0
                                ? "Searches running now ✓"
                                : "No active watches to poll";
                    }
                    if (b.toast) {
                        b.toast(
                            r.started > 0
                                ? "Started a poll cycle on " + r.started + " active watch(es). Results show up in Recent finds within ~30 sec."
                                : "No active watches yet. Create one on the Saved searches tab.",
                            "success",
                        );
                    }
                } else if (r && r.error === "rate_limited") {
                    if (originalText) {
                        originalText.textContent =
                            r.message || "Wait a moment, try again";
                    }
                    if (b.toast) b.toast(r.message || "Rate limited.", "info");
                } else {
                    if (originalText) originalText.textContent = "Try again";
                    if (b.toast) b.toast("Could not start polling. Try again.", "error");
                }
            } catch (e) {
                if (originalText) originalText.textContent = "Try again";
                if (b.toast) b.toast(b.describeError ? b.describeError(e) : String(e), "error");
            }
            // Re-enable + restore label after 60s — matches the server-
            // side rate-limit window so the button stops looking
            // permanently broken if the click was throttled.
            setTimeout(function () {
                btn.disabled = false;
                if (originalText) originalText.textContent = originalLabel;
            }, 60000);
        });
    }

    // ===================================================================
    // Personalized insights — moved here from tab_insights.js on
    // 2026-05-07 so users actually see them. Six widgets unlock as the
    // user hits achievement milestones; each card self-hides until both
    // unlocked AND we have data to show. The whole section is hidden
    // when nothing's unlocked yet (fresh installs see no empty cards).
    //
    // Renders identical to the version that briefly lived on the
    // Insights tab — kept here standalone so we don't pull tab_insights.js
    // on every Home load.
    // ===================================================================

    var INSIGHT_CARDS = [
        { unlock: "score_distribution", el: "insight-score-distribution" },
        { unlock: "favorite_category",  el: "insight-favorite-category"  },
        { unlock: "favorite_term",      el: "insight-favorite-term"      },
        { unlock: "hunt_rhythm",        el: "insight-hunt-rhythm"        },
        { unlock: "savings_velocity",   el: "insight-savings-velocity"   },
        { unlock: "lifetime_chart",     el: "insight-lifetime-chart"     },
    ];

    async function loadPersonalInsights() {
        var section = document.getElementById("personal-insights-section");
        if (!section) return;
        var unlocked = new Set();
        var payload = null;
        try {
            var results = await Promise.all([
                b.apiGet("/api/achievements"),
                b.apiGet("/api/insights/personal"),
            ]);
            var gallery = results[0];
            payload = results[1];
            var items = (gallery && (gallery.items || gallery.achievements)) || [];
            items.forEach(function (it) {
                if (it.unlocked_at && it.unlocks) {
                    unlocked.add(it.unlocks);
                }
            });
        } catch (e) {
            section.hidden = true;
            return;
        }
        var anyShown = false;
        INSIGHT_CARDS.forEach(function (def) {
            var card = document.getElementById(def.el);
            if (!card) return;
            var data = payload && payload[def.unlock];
            var isUnlocked = unlocked.has(def.unlock);
            if (!isUnlocked || !data) {
                card.hidden = true;
                return;
            }
            card.hidden = false;
            anyShown = true;
            renderInsight(def.unlock, data);
        });
        // Hide the entire section when no widgets to render — keeps
        // the home page tidy for fresh installs.
        section.hidden = !anyShown;
    }

    function renderInsight(key, data) {
        switch (key) {
            case "score_distribution":  return renderScoreDistribution(data);
            case "favorite_category":   return renderFavoriteCategory(data);
            case "favorite_term":       return renderFavoriteTerm(data);
            case "hunt_rhythm":         return renderHuntRhythm(data);
            case "savings_velocity":    return renderSavingsVelocity(data);
            case "lifetime_chart":      return renderLifetimeChart(data);
        }
    }

    function renderScoreDistribution(d) {
        var sub = document.getElementById("insight-score-distribution-sub");
        var chart = document.getElementById("insight-score-distribution-chart");
        if (!d || !d.buckets || !chart) return;
        if (sub) sub.textContent = d.total + " scored listings";
        var max = Math.max.apply(null, d.buckets.map(function (x) { return x.count; })) || 1;
        var W = 280, H = 80, BAR_W = (W - 9 * 2) / 10;
        var byBucket = {};
        d.buckets.forEach(function (x) { byBucket[x.bucket] = x.count; });
        var parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="score distribution histogram">'];
        for (var i = 0; i < 10; i++) {
            var cnt = byBucket[i] || 0;
            var h = cnt === 0 ? 1 : Math.max(2, (cnt / max) * (H - 12));
            var x = i * (BAR_W + 2);
            var y = H - h - 10;
            var fill = i >= 8 ? "#16a34a" : (i >= 7 ? "#65a30d" : (i >= 5 ? "#a3a3a3" : "#d4d4d4"));
            parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + BAR_W.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="1" fill="' + fill + '"/>');
            parts.push('<text x="' + (x + BAR_W / 2).toFixed(1) + '" y="' + (H - 1) + '" text-anchor="middle" font-size="9" fill="#6b5d52">' + (i * 10) + '</text>');
        }
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    function renderFavoriteCategory(d) {
        if (!d || !d.term) return;
        var nameEl = document.getElementById("insight-favorite-category-name");
        var metaEl = document.getElementById("insight-favorite-category-meta");
        if (nameEl) nameEl.textContent = d.term;
        if (metaEl) {
            metaEl.textContent =
                d.hits + " 80+ hit" + (d.hits === 1 ? "" : "s") +
                " · avg score " + (d.avg_score || "—") +
                " · " + b.fmtMoney(d.savings) + " saved";
        }
    }

    function renderFavoriteTerm(d) {
        if (!d || !d.term) return;
        var nameEl = document.getElementById("insight-favorite-term-name");
        var metaEl = document.getElementById("insight-favorite-term-meta");
        if (nameEl) nameEl.textContent = d.term;
        if (metaEl) {
            metaEl.textContent =
                d.hits + " 80+ hit" + (d.hits === 1 ? "" : "s") +
                " · " + b.fmtMoney(d.savings) + " saved on this term alone";
        }
    }

    function renderHuntRhythm(d) {
        var chart = document.getElementById("insight-hunt-rhythm-chart");
        if (!chart || !d || !d.days) return;
        var max = Math.max.apply(null, d.days.map(function (x) { return x.count; })) || 1;
        var labels = ["S", "M", "T", "W", "T", "F", "S"];
        var W = 280, H = 80, BAR_W = (W - 6 * 6) / 7;
        var parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="day-of-week distribution">'];
        d.days.forEach(function (day, i) {
            var h = day.count === 0 ? 1 : Math.max(2, (day.count / max) * (H - 16));
            var x = i * (BAR_W + 6);
            var y = H - h - 14;
            var isWeekend = (day.dow === 0 || day.dow === 6);
            parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + BAR_W.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2" fill="' + (isWeekend ? "#c2410c" : "#1a1614") + '"/>');
            parts.push('<text x="' + (x + BAR_W / 2).toFixed(1) + '" y="' + (H - 2) + '" text-anchor="middle" font-size="10" font-weight="500" fill="#6b5d52">' + labels[i] + '</text>');
        });
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    function renderSavingsVelocity(d) {
        var chart = document.getElementById("insight-savings-velocity-chart");
        var sub = document.getElementById("insight-savings-velocity-sub");
        if (!chart || !d || !d.weeks || !d.weeks.length) return;
        if (sub) sub.textContent = "last 12 weeks · " + b.fmtMoney(d.total_savings) + " total";
        var max = Math.max(d.max_weekly_savings || 1, 1);
        var W = 280, H = 90, BAR_W = (W - (d.weeks.length - 1) * 3) / Math.max(d.weeks.length, 1);
        var parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="weekly savings, last 12 weeks">'];
        d.weeks.forEach(function (week, i) {
            var h = week.savings === 0 ? 1 : Math.max(2, (week.savings / max) * (H - 8));
            var x = i * (BAR_W + 3);
            var y = H - h - 4;
            parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + BAR_W.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="1.5" fill="#c2410c"/>');
        });
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    function renderLifetimeChart(d) {
        var chart = document.getElementById("insight-lifetime-chart-chart");
        var sub = document.getElementById("insight-lifetime-chart-sub");
        if (!chart || !d || !d.points || !d.points.length) return;
        if (sub) sub.textContent = "cumulative · " + b.fmtMoney(d.total_lifetime_savings) + " all-time";
        var points = d.points;
        var max = points[points.length - 1].cumulative_savings || 1;
        var W = 280, H = 90;
        var stepX = points.length > 1 ? W / (points.length - 1) : W;
        var pathD = "M 0," + H;
        points.forEach(function (p, i) {
            var x = i * stepX;
            var y = H - (p.cumulative_savings / max) * (H - 4);
            pathD += " L " + x.toFixed(1) + "," + y.toFixed(1);
        });
        pathD += " L " + W + "," + H + " Z";
        var parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="cumulative lifetime savings">'];
        parts.push('<path d="' + pathD + '" fill="rgba(194,65,12,0.18)" stroke="#c2410c" stroke-width="1.5"/>');
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    /* Quick teaser on the home button: "X Pro days banked" — drops
       the "X of Y unlocked" framing per user feedback (they didn't
       want the total upper bound surfaced; just show what they've
       earned so far so it reads as positive momentum). 2026-05-07. */
    async function loadAchievementsTeaser() {
        var span = document.getElementById("home-ach-progress");
        if (!span) return;
        try {
            var res = await b.apiGet("/api/achievements");
            if (res && res.ok) {
                var banked = res.pro_days_banked != null ? res.pro_days_banked : 0;
                span.textContent =
                    "(" + banked + " Pro day" + (banked === 1 ? "" : "s") +
                    " banked so far)";
            }
        } catch (e) { /* silent */ }
    }

    /* ---------- Pro-vs-Free comparison (trial users only) ----------
       Pulls /api/dashboard/summary and shows the user the Pro-only
       features they're actively using right now. Visible value.       */
    async function loadProVsFree() {
        var grid = document.getElementById("pro-vs-free-grid");
        if (!grid) return;
        try {
            var s = await b.apiGet("/api/dashboard/summary");
            var watchesActive = s.active_watches || 0;
            var pollsToday = (s.rates && s.rates.polls_last_1h) || 0;
            var f = s.funnel_today || {};
            var dealsToday = f.appraised || 0;
            var alertsToday = (s.rates && s.rates.emails_today) || 0;

            // Each tile: what you're doing now vs what Free caps at.
            var tiles = [
                {
                    metric: watchesActive,
                    label: "active saved searches",
                    free_cap: "Free caps at 3",
                    over: watchesActive > 3,
                },
                {
                    metric: dealsToday,
                    label: "deals scored today",
                    free_cap: "Polled every 5 min on Pro · 30 min on Free",
                    over: false,
                },
                {
                    metric: alertsToday,
                    label: "instant alerts sent",
                    free_cap: "Free is daily 8am digest only",
                    over: alertsToday > 0,
                },
            ];
            grid.innerHTML = tiles.map(function (t) {
                return '<div class="pvf-tile">' +
                    '<div class="pvf-num">' + t.metric + '</div>' +
                    '<div class="pvf-label">' + b.escapeHTML(t.label) + '</div>' +
                    '<div class="pvf-cap muted">' + b.escapeHTML(t.free_cap) + '</div>' +
                '</div>';
            }).join("");
        } catch (e) {
            grid.innerHTML = '<div class="muted">Stats loading…</div>';
        }
    }

    /* ---------- savings achievement trigger ------------------------
       Watches /api/insights/lifetime — when total savings cross a
       threshold, fires an idempotent /api/achievements proxy call so
       the cloud awards the savings_X milestone. Idempotent server
       side: repeat calls after first grant return awarded:false. */
    async function checkSavingsAchievements() {
        try {
            var res = await b.apiGet("/api/insights/lifetime");
            var saved = (res && res.savings_total) || 0;
            // Thresholds retuned 2026-05-07 (5–10x bump). IDs renamed
            // to match the new amounts. Old IDs (savings_100, etc.)
            // are no longer client-grantable on the cloud side, so
            // any older desktop build hitting them just sees 400s.
            var thresholds = [
                { id: "savings_500",   amount: 500    },
                { id: "savings_2500",  amount: 2500   },
                { id: "savings_10k",   amount: 10000  },
                { id: "savings_50k",   amount: 50000  },
                { id: "savings_100k",  amount: 100000 },
            ];
            for (var i = 0; i < thresholds.length; i++) {
                if (saved >= thresholds[i].amount) {
                    // POST goes through the Origin-checked /api/* gate.
                    // We don't actually have a /api/achievements/award
                    // proxy yet — skip directly to the cloud via a
                    // quick fetch (achievements are non-critical).
                    try {
                        await b.apiPost("/api/achievements/award",
                            { action_id: thresholds[i].id });
                    } catch (_) { /* best-effort, ignore */ }
                }
            }
        } catch (e) { /* silent */ }
    }

    document.addEventListener("DOMContentLoaded", function () {
        wireRedeem();
        wireAchievementsButton();
        wirePollNowButton();
        // Breakdown-modal close handlers are wired by app_shell.html's
        // inline script (X / backdrop onclick) + Escape listener.
        loadStats();
        loadCadence();
        loadStreak();
        loadRecent();
        loadHotDeal();
        loadHomeInsights();
        loadSavingsFlex();
        loadTopWatches();
        loadAchievementsTeaser();
        loadProVsFree();
        loadPersonalInsights();
        checkSavingsAchievements();
        b.setIntervalVisible(loadStats, 30000);
        b.setIntervalVisible(loadCadence, 60000);  // 24h average — slow-moving
        b.setIntervalVisible(loadRecent, 30000);   // pick up new threshold hits live
        b.setIntervalVisible(loadHotDeal, 30000);  // hot-deal banner re-checks every 30s
        b.setIntervalVisible(loadHomeInsights, 60000);
        b.setIntervalVisible(loadSavingsFlex, 60000);
        b.setIntervalVisible(loadTopWatches, 60000);
        b.setIntervalVisible(loadAchievementsTeaser, 60000);
        b.setIntervalVisible(loadProVsFree, 30000);
        b.setIntervalVisible(loadPersonalInsights, 90000);  // unlocks + payloads — slow-moving
        b.setIntervalVisible(checkSavingsAchievements, 5 * 60000);
    });
})();
