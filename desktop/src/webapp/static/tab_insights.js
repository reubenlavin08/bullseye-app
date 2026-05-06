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
        var url = it.listing_url || "#";
        var title = b.escapeHTML(it.title || "(untitled)");
        var sav = (it.savings != null && it.savings > 0)
            ? "saved " + b.fmtMoney(it.savings)
            : "";
        var loc = it.seller_location ? b.escapeHTML(it.seller_location) : "";
        var meta = [b.fmtMoney(it.price), sav, loc].filter(Boolean).join(" · ");
        return '<div class="lb-row">'
            + '<div class="lb-rank">' + rank + '</div>'
            + photo
            + '<div class="lb-body">'
            +   '<div class="lb-title"><a href="' + url + '" target="_blank" rel="noopener">' + title + '</a></div>'
            +   '<div class="lb-meta muted">' + meta + '</div>'
            + '</div>'
            + '<div class="lb-score ' + b.scoreClass(it.deal_score) + '">'
            +   b.fmtScore(it.deal_score)
            + '</div>'
            + '</div>';
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
                    var url = it.listing_url || "#";
                    var title = b.escapeHTML(it.title || "(untitled)");
                    var price = b.fmtMoney(it.price);
                    var sav = (it.savings != null && it.savings > 0)
                        ? "saved " + b.fmtMoney(it.savings) : "";
                    return '<div class="hd-row">' +
                        '<div class="hd-score ' + b.scoreClass(it.deal_score) + '">' +
                        b.fmtScore(it.deal_score) +
                        '</div>' +
                        '<div class="hd-body">' +
                        '<div class="hd-title"><a href="' + url + '" target="_blank" rel="noopener">' + title + '</a></div>' +
                        '<div class="hd-meta muted">' + price + (sav ? ' · ' + sav : '') + '</div>' +
                        '</div>' +
                        '</div>';
                }).join("");
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

    document.addEventListener("DOMContentLoaded", function () {
        loadHeatmap().then(wireHeatmapClicks);
        loadLeaderboard().then(wireLeaderboardClicks);
    });
})();
