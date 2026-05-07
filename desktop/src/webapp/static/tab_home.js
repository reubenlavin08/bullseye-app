/* Home tab — pulls a couple of cheap endpoints and stamps numbers
   into stat cards. Re-uses /api/dashboard/summary so we don't need a
   new server route. */
(function () {
    "use strict";
    var b = window.bullseye;

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
            document.getElementById("stat-watches-active").textContent =
                s.active_watches != null ? s.active_watches : "—";
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

    async function loadStreak() {
        try {
            var s = await b.apiGet("/api/streak");
            // Cloud Edge Function returns `current_streak`, not
            // `current_streak_days` — accept both for forward-compat.
            var days = s.current_streak;
            if (days == null) days = s.current_streak_days;
            document.getElementById("streak-days").textContent =
                days != null ? days : "0";
            var nextEl = document.getElementById("streak-next");
            if (s.pro_days_banked != null) {
                nextEl.textContent = "· " + s.pro_days_banked + " Pro day(s) banked";
            } else {
                nextEl.textContent = "";
            }
        } catch (e) {
            // streak unavailable — hide the row, never crash the page
            var card = document.getElementById("streak-days");
            if (card) card.textContent = "—";
        }
    }

    async function loadRecent() {
        var feed = document.getElementById("recent-activity");
        if (!feed) return;
        try {
            var s = await b.apiGet("/api/dashboard/appraisal-feed?limit=10&filter=scored");
            var items = (s && s.items) || s.rows || [];
            if (!items.length) {
                feed.innerHTML = '<div class="muted">No scored listings yet.</div>';
                return;
            }
            feed.innerHTML = items.slice(0, 10).map(function (it) {
                var score = it.deal_score;
                var scoreCls = b.scoreClass(score);
                var url = b.safeUrl(it.listing_url);
                var title = b.escapeHTML(it.title || "(untitled)");
                var rel = b.fmtRelative(it.scraped_at);
                var price = b.fmtMoney(it.price);
                return '<div class="activity-row">'
                    + '<div class="activity-score ' + scoreCls + '">' + b.fmtScore(score) + '</div>'
                    + '<div class="activity-title"><a href="' + b.escapeHTML(url) + '" target="_blank" rel="noopener">' + title + '</a>'
                    + '<div class="muted" style="font-size:11px;">' + price + ' · ' + b.escapeHTML(it.keyword || "") + '</div></div>'
                    + '<div class="activity-meta">' + rel + '</div>'
                    + '</div>';
            }).join("");
        } catch (e) {
            feed.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
        }
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
            if (res.cultural_flex) {
                flexEl.textContent = "That's enough to buy " + res.cultural_flex + ".";
                flexEl.hidden = false;
            } else {
                flexEl.hidden = true;
            }
        } catch (e) {
            section.hidden = true;
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        wireRedeem();
        loadStats();
        loadStreak();
        loadRecent();
        loadHomeInsights();
        loadSavingsFlex();
        loadTopWatches();
        // Refresh stats + insights every 30s while the tab is open.
        setInterval(loadStats, 30000);
        setInterval(loadHomeInsights, 60000);
        setInterval(loadSavingsFlex, 60000);
        setInterval(loadTopWatches, 60000);
    });
})();
