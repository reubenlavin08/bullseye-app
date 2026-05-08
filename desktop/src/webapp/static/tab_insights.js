/* Insights tab — 90-day deal heatmap + personal-best leaderboard.
   Mirrors Wispr Flow's Insights surface: streak heatmap (calendar grid)
   plus a top-N list with thumbnail and score. No remote calls beyond the
   two /api/insights/* endpoints. */
(function () {
    "use strict";
    var b = window.bullseye;

    /* ---------- heatmap ----------------------------------------------
       Grid layout: GitHub-style. Each cell = 1 day. Columns are weeks
       (oldest left → newest right). Rows are weekdays (Sun top → Sat).
       Cells are color-bucketed by deal count: 0 / 1 / 2-3 / 4-6 / 7+.
    ----------------------------------------------------------------- */

    function bucket(n) {
        if (!n || n <= 0) return 0;
        if (n === 1) return 1;
        if (n <= 3) return 2;
        if (n <= 6) return 3;
        return 4;
    }

    function dayOfWeek(iso) {
        // Use UTC so the cell positions are stable regardless of TZ.
        var d = new Date(iso + "T00:00:00Z");
        return d.getUTCDay(); // 0 = Sun, 6 = Sat
    }

    function buildHeatmapSvg(days) {
        // Pad the leading week so the first column starts on Sunday.
        // Each cell is 12x12 with 2px gap. 7 rows tall.
        var CELL = 12;
        var GAP = 3;
        var ROWS = 7;
        var first = days[0];
        var leadingPad = first ? dayOfWeek(first.date) : 0;

        var totalCells = leadingPad + days.length;
        var cols = Math.ceil(totalCells / ROWS);
        var width = cols * (CELL + GAP);
        var height = ROWS * (CELL + GAP);

        var svgParts = [
            '<svg class="heatmap-svg" viewBox="0 0 ' + width + ' ' + height
                + '" width="100%" height="' + height + '" role="img" aria-label="90-day activity heatmap">',
        ];

        days.forEach(function (cell, i) {
            var idx = leadingPad + i;
            var col = Math.floor(idx / ROWS);
            var row = idx % ROWS;
            var x = col * (CELL + GAP);
            var y = row * (CELL + GAP);
            var lvl = bucket(cell.deals);
            var titleText = cell.date + ": "
                + (cell.deals === 1 ? "1 deal" : cell.deals + " deals")
                + (cell.alerts ? " · " + cell.alerts + " alert(s)" : "");
            svgParts.push(
                '<rect class="hm-cell hm-l' + lvl + '" '
                + 'x="' + x + '" y="' + y + '" '
                + 'width="' + CELL + '" height="' + CELL + '" '
                + 'rx="2" ry="2" '
                + 'data-deals="' + cell.deals + '" '
                + 'data-date="' + b.escapeHTML(cell.date) + '">'
                + '<title>' + b.escapeHTML(titleText) + '</title>'
                + '</rect>'
            );
        });

        svgParts.push('</svg>');
        return svgParts.join("");
    }

    async function loadHeatmap() { return _loadHeatmap(); }
    async function _loadHeatmap() {
        var wrap = document.getElementById("heatmap-wrap");
        try {
            var res = await b.apiGet("/api/insights/heatmap");
            var days = res.days || [];
            if (!days.length) {
                wrap.innerHTML = '<div class="muted">No activity yet. Save a watch and check back tomorrow.</div>';
                return;
            }
            wrap.innerHTML = buildHeatmapSvg(days);

            var s = res.summary || {};
            document.getElementById("summary-current-streak").textContent =
                s.current_streak != null ? s.current_streak : "0";
            document.getElementById("summary-longest-streak").textContent =
                s.longest_streak != null ? s.longest_streak : "0";
            document.getElementById("summary-deals").textContent =
                s.total_deals != null ? s.total_deals : "0";
            document.getElementById("summary-active-days").textContent =
                s.active_days != null ? s.active_days : "0";
            document.getElementById("heatmap-threshold").textContent =
                res.threshold != null ? "≥ " + res.threshold : "—";
        } catch (e) {
            wrap.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
        }
    }

    /* ---------- leaderboard ------------------------------------------
       Thin row-list, photo thumb on the left, score badge, title +
       savings, link out to the original listing.
    ----------------------------------------------------------------- */

    function renderRow(it, rank) {
        var photo = it.photo_url
            ? '<img class="lb-photo" src="' + b.escapeHTML(it.photo_url) + '" alt="" loading="lazy">'
            : '<div class="lb-photo lb-photo-empty">—</div>';
        var url = b.safeUrl(it.listing_url);
        var title = b.escapeHTML(it.title || "(untitled)");
        var sav = (it.savings != null && it.savings > 0)
            ? "saved " + b.fmtMoney(it.savings)
            : "";
        var loc = it.seller_location ? b.escapeHTML(it.seller_location) : "";
        var meta = [b.fmtMoney(it.price), sav, loc].filter(Boolean).join(" · ");
        // 2026-05-07: score badge is now a button that opens the
        // breakdown modal (same as on /home and /activity). Plus an
        // explicit "Score breakdown ↗" text link below the meta line
        // so the affordance isn't hidden behind the badge alone.
        var lid = b.escapeHTML(String(it.id || ""));
        var bdLink = (it.deal_score != null && it.id)
            ? '<button type="button" class="insights-bd-link"'
              + ' data-listing-id="' + lid + '">'
              + 'Score breakdown <span aria-hidden="true">&rarr;</span></button>'
            : '';
        return '<div class="lb-row" data-listing-id="' + lid + '">'
            + '<div class="lb-rank">' + rank + '</div>'
            + photo
            + '<div class="lb-body">'
            +   '<div class="lb-title"><a href="' + b.escapeHTML(url) + '" target="_blank" rel="noopener">' + title + '</a></div>'
            +   '<div class="lb-meta muted">' + meta + '</div>'
            +   bdLink
            + '</div>'
            + '<button type="button" class="lb-score lb-score-btn ' + b.scoreClass(it.deal_score) + '"'
            +   ' data-listing-id="' + lid + '"'
            +   ' title="Click for score breakdown + eBay comps">'
            +   b.fmtScore(it.deal_score)
            + '</button>'
            + '</div>';
    }

    /* Wire any element with data-listing-id inside `root` to open the
       breakdown modal on click. Idempotent — instances flagged via
       _bdWired so re-rendering doesn't double-bind. Used by both the
       leaderboard and the heatmap day-panel. */
    function wireBreakdownClicks(root) {
        if (!root) return;
        var sel = ".lb-score-btn, .insights-bd-link, .hd-score-btn, .hd-bd-link";
        root.querySelectorAll(sel).forEach(function (btn) {
            if (btn._bdWired) return;
            btn._bdWired = true;
            btn.addEventListener("click", function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                var lid = btn.getAttribute("data-listing-id");
                if (lid && b.openBreakdownModal) {
                    b.openBreakdownModal(lid);
                }
            });
        });
    }

    async function loadLeaderboard() {
        var el = document.getElementById("leaderboard");
        try {
            var res = await b.apiGet("/api/insights/personal-best");
            var items = res.items || [];
            if (!items.length) {
                el.innerHTML = '<div class="muted">No scored deals yet. Save a watch and the scheduler will fill this list as it finds matches.</div>';
                return;
            }
            el.innerHTML = items.map(function (it, i) {
                return renderRow(it, i + 1);
            }).join("");
            wireBreakdownClicks(el);
        } catch (e) {
            el.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
        }
    }

    /* ---------- interactivity (D7 polish) ----------------------------
       Heatmap cells click → fetch the day's scored deals inline.
       Leaderboard rows click → expand inline with full score breakdown. */

    function wireHeatmapClicks() {
        var wrap = document.getElementById("heatmap-wrap");
        var panel = document.getElementById("heatmap-day-panel");
        var label = document.getElementById("heatmap-day-label");
        var list = document.getElementById("heatmap-day-list");
        var closeBtn = document.getElementById("heatmap-day-close");
        if (!wrap || !panel) return;

        wrap.addEventListener("click", async function (ev) {
            var cell = ev.target.closest(".hm-cell");
            if (!cell) return;
            var date = cell.getAttribute("data-date");
            var deals = parseInt(cell.getAttribute("data-deals") || "0", 10);
            if (!date) return;
            label.textContent = date + " · " + (deals || 0) + (deals === 1 ? " deal" : " deals");
            panel.hidden = false;
            list.innerHTML = '<div class="muted">loading…</div>';
            // Highlight the active cell
            wrap.querySelectorAll(".hm-cell.is-active").forEach(function (c) {
                c.classList.remove("is-active");
            });
            cell.classList.add("is-active");
            try {
                var res = await b.apiGet(
                    "/api/dashboard/appraisal-feed?limit=20&filter=scored&date="
                    + encodeURIComponent(date)
                );
                var items = (res && (res.items || res.rows)) || [];
                if (!items.length) {
                    list.innerHTML = '<div class="muted">No scored deals on this day.</div>';
                    return;
                }
                list.innerHTML = items.map(function (it) {
                    var url = b.safeUrl(it.listing_url);
                    var title = b.escapeHTML(it.title || "(untitled)");
                    var price = b.fmtMoney(it.price);
                    var sav = (it.savings != null && it.savings > 0)
                        ? "saved " + b.fmtMoney(it.savings) : "";
                    var lid = b.escapeHTML(String(it.id || ""));
                    var bdLink = (it.deal_score != null && it.id)
                        ? '<button type="button" class="hd-bd-link insights-bd-link"'
                          + ' data-listing-id="' + lid + '">'
                          + 'Score breakdown <span aria-hidden="true">&rarr;</span></button>'
                        : '';
                    return '<div class="hd-row" data-listing-id="' + lid + '">' +
                        '<button type="button" class="hd-score hd-score-btn '
                        + b.scoreClass(it.deal_score) + '"'
                        + ' data-listing-id="' + lid + '"'
                        + ' title="Click for score breakdown">' +
                        b.fmtScore(it.deal_score) +
                        '</button>' +
                        '<div class="hd-body">' +
                        '<div class="hd-title"><a href="' + b.escapeHTML(url) + '" target="_blank" rel="noopener">' + title + '</a></div>' +
                        '<div class="hd-meta muted">' + price + (sav ? ' · ' + sav : '') + '</div>' +
                        bdLink +
                        '</div>' +
                        '</div>';
                }).join("");
                wireBreakdownClicks(list);
            } catch (e) {
                list.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
            }
        });

        closeBtn.addEventListener("click", function () {
            panel.hidden = true;
            wrap.querySelectorAll(".hm-cell.is-active").forEach(function (c) {
                c.classList.remove("is-active");
            });
        });
    }

    function wireLeaderboardClicks() {
        var el = document.getElementById("leaderboard");
        if (!el) return;
        el.addEventListener("click", function (ev) {
            // Don't intercept clicks on the actual listing link
            if (ev.target.closest("a")) return;
            var row = ev.target.closest(".lb-row");
            if (!row) return;
            row.classList.toggle("is-expanded");
            // Lazy: just expose more meta, no extra fetch
        });
    }

    // ===================================================================
    // Personalized insights moved to Home 2026-05-07 — see tab_home.js
    // for the full implementation. Insights tab keeps the analytical
    // heatmap + leaderboard; Home owns the unlockable retention
    // widgets where users will actually see them.
    // ===================================================================

    const INSIGHT_CARDS_DEPRECATED = [];  // moved
    async function loadPersonalInsights_DEPRECATED() {
        return;
    }

    /* eslint-disable no-unreachable, no-unused-vars */
    async function _legacy_loadPersonalInsights() {
        const emptyMsg = document.getElementById("insight-empty-msg");
        let unlocked = new Set();
        let payload = null;
        try {
            // Run in parallel: gallery for unlock state + personal-insights
            // payload for the actual numbers/charts.
            const [gallery, p] = await Promise.all([
                b.apiGet("/api/achievements"),
                b.apiGet("/api/insights/personal"),
            ]);
            payload = p;
            // achievements payload looks like { items: [{id, unlocks, unlocked_at}, ...] }
            // Anything with unlocked_at != null AND a non-null `unlocks`
            // key counts as a usable insight unlock.
            const items = (gallery && (gallery.items || gallery.achievements)) || [];
            items.forEach(function (it) {
                if (it.unlocked_at && it.unlocks) {
                    unlocked.add(it.unlocks);
                }
            });
        } catch (e) {
            // Endpoint failure → leave all cards hidden, message visible.
            return;
        }

        let anyShown = false;
        INSIGHT_CARDS.forEach(function (def) {
            const card = document.getElementById(def.el);
            if (!card) return;
            const data = payload && payload[def.unlock];
            const isUnlocked = unlocked.has(def.unlock);
            // Hide if not unlocked OR no data yet. Both render as the
            // same "card hidden" state — the user finds out about it
            // through the achievement gallery, not by seeing empty cards.
            if (!isUnlocked || !data) {
                card.hidden = true;
                return;
            }
            card.hidden = false;
            anyShown = true;
            renderInsight(def.unlock, data);
        });
        if (emptyMsg) emptyMsg.hidden = anyShown;
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
        const sub = document.getElementById("insight-score-distribution-sub");
        const chart = document.getElementById("insight-score-distribution-chart");
        if (!d || !d.buckets || !chart) return;
        if (sub) sub.textContent = d.total + " scored listings";
        const max = Math.max.apply(null, d.buckets.map(function (b) { return b.count; })) || 1;
        const W = 280, H = 80, BAR_W = (W - 9 * 2) / 10;
        // Render 10 buckets (0-9, 10-19, …, 90-99). The 99 bucket
        // also captures the rare 100 score.
        const byBucket = {};
        d.buckets.forEach(function (b) { byBucket[b.bucket] = b.count; });
        const parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="score distribution histogram">'];
        for (let i = 0; i < 10; i++) {
            const cnt = byBucket[i] || 0;
            const h = cnt === 0 ? 1 : Math.max(2, (cnt / max) * (H - 12));
            const x = i * (BAR_W + 2);
            const y = H - h - 10;
            const fill = i >= 8 ? "#16a34a" : (i >= 7 ? "#65a30d" : (i >= 5 ? "#a3a3a3" : "#d4d4d4"));
            parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + BAR_W.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="1" fill="' + fill + '"/>');
            parts.push('<text x="' + (x + BAR_W / 2).toFixed(1) + '" y="' + (H - 1) + '" text-anchor="middle" font-size="9" fill="#6b5d52">' + (i * 10) + '</text>');
        }
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    function renderFavoriteCategory(d) {
        if (!d || !d.term) return;
        const nameEl = document.getElementById("insight-favorite-category-name");
        const metaEl = document.getElementById("insight-favorite-category-meta");
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
        const nameEl = document.getElementById("insight-favorite-term-name");
        const metaEl = document.getElementById("insight-favorite-term-meta");
        if (nameEl) nameEl.textContent = d.term;
        if (metaEl) {
            metaEl.textContent =
                d.hits + " 80+ hit" + (d.hits === 1 ? "" : "s") +
                " · " + b.fmtMoney(d.savings) + " saved on this term alone";
        }
    }

    function renderHuntRhythm(d) {
        const chart = document.getElementById("insight-hunt-rhythm-chart");
        if (!chart || !d || !d.days) return;
        const max = Math.max.apply(null, d.days.map(function (x) { return x.count; })) || 1;
        const labels = ["S", "M", "T", "W", "T", "F", "S"];
        const W = 280, H = 80, BAR_W = (W - 6 * 6) / 7;
        const parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="day-of-week distribution">'];
        d.days.forEach(function (day, i) {
            const h = day.count === 0 ? 1 : Math.max(2, (day.count / max) * (H - 16));
            const x = i * (BAR_W + 6);
            const y = H - h - 14;
            const isWeekend = (day.dow === 0 || day.dow === 6);
            parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + BAR_W.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2" fill="' + (isWeekend ? "#c2410c" : "#1a1614") + '"/>');
            parts.push('<text x="' + (x + BAR_W / 2).toFixed(1) + '" y="' + (H - 2) + '" text-anchor="middle" font-size="10" font-weight="500" fill="#6b5d52">' + labels[i] + '</text>');
        });
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    function renderSavingsVelocity(d) {
        const chart = document.getElementById("insight-savings-velocity-chart");
        const sub = document.getElementById("insight-savings-velocity-sub");
        if (!chart || !d || !d.weeks || !d.weeks.length) return;
        if (sub) {
            sub.textContent = "last 12 weeks · " + b.fmtMoney(d.total_savings) + " total";
        }
        const max = Math.max(d.max_weekly_savings || 1, 1);
        const W = 280, H = 90, BAR_W = (W - (d.weeks.length - 1) * 3) / Math.max(d.weeks.length, 1);
        const parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="weekly savings, last 12 weeks">'];
        d.weeks.forEach(function (week, i) {
            const h = week.savings === 0 ? 1 : Math.max(2, (week.savings / max) * (H - 8));
            const x = i * (BAR_W + 3);
            const y = H - h - 4;
            parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + BAR_W.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="1.5" fill="#c2410c"/>');
        });
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    function renderLifetimeChart(d) {
        const chart = document.getElementById("insight-lifetime-chart-chart");
        const sub = document.getElementById("insight-lifetime-chart-sub");
        if (!chart || !d || !d.points || !d.points.length) return;
        if (sub) {
            sub.textContent = "cumulative · " + b.fmtMoney(d.total_lifetime_savings) + " all-time";
        }
        const points = d.points;
        const max = points[points.length - 1].cumulative_savings || 1;
        const W = 280, H = 90;
        const stepX = points.length > 1 ? W / (points.length - 1) : W;
        // Stepped area chart — series of (x,y) points then a closing
        // path back to baseline.
        let pathD = "M 0," + H;
        points.forEach(function (p, i) {
            const x = i * stepX;
            const y = H - (p.cumulative_savings / max) * (H - 4);
            pathD += " L " + x.toFixed(1) + "," + y.toFixed(1);
        });
        pathD += " L " + W + "," + H + " Z";
        const parts = ['<svg viewBox="0 0 ' + W + ' ' + H + '" width="100%" height="' + H + '" role="img" aria-label="cumulative lifetime savings">'];
        parts.push('<path d="' + pathD + '" fill="rgba(194,65,12,0.18)" stroke="#c2410c" stroke-width="1.5"/>');
        parts.push('</svg>');
        chart.innerHTML = parts.join("");
    }

    document.addEventListener("DOMContentLoaded", function () {
        loadHeatmap().then(wireHeatmapClicks);
        loadLeaderboard().then(wireLeaderboardClicks);
    });
})();
