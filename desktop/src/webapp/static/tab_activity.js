/* Activity tab — chronological feed via /api/dashboard/appraisal-feed.
   Filters live client-side around the existing query params (filter,
   min_score, since_id). */
(function () {
    "use strict";
    var b = window.bullseye;

    var listEl = document.getElementById("activity-list");
    var lastId = null;
    var loading = false;

    function buildQuery(append) {
        var qs = [];
        var filter = document.getElementById("filter-status").value;
        var minScore = document.getElementById("filter-min").value.trim();
        qs.push("limit=50");
        if (filter && filter !== "all") qs.push("filter=" + encodeURIComponent(filter));
        if (minScore) qs.push("min_score=" + encodeURIComponent(minScore));
        if (append && lastId) qs.push("since_id=" + encodeURIComponent(lastId));
        return "/api/dashboard/appraisal-feed?" + qs.join("&");
    }

    function renderItem(it) {
        var photo = it.photo_url
            ? '<img class="photo" src="' + b.escapeHTML(it.photo_url) + '" alt="" loading="lazy">'
            : '<div class="photo photo-empty">no photo</div>';
        var score = it.deal_score;
        var rejected = it.rejected;
        var unscoreable = (it.appraised && score == null && !rejected);
        // The score block is now a button (clickable) when we have a
        // numeric score — opens the breakdown modal in shell.js. For
        // rejected / unscoreable rows we keep a plain div since
        // there's nothing useful to break down. Wired up 2026-05-07
        // per user feedback "nowhere on my listings can I click to
        // see the score breakdown and see their comps."
        var lid = b.escapeHTML(String(it.id || ""));
        var scoreBlock;
        if (rejected) {
            scoreBlock = '<div class="score-block"><div class="score-num" style="color:var(--bad);">REJ</div><div class="score-label">rejected</div></div>';
        } else if (unscoreable) {
            scoreBlock = '<div class="score-block"><div class="score-num muted">∅</div><div class="score-label">no data</div></div>';
        } else {
            scoreBlock = '<button type="button" class="score-block score-block-btn"'
                + ' data-listing-id="' + lid + '"'
                + ' title="Click for score breakdown + eBay comps">'
                + '<div class="score-num ' + b.scoreClass(score) + '">'
                + b.fmtScore(score) + '</div><div class="score-label">score</div>'
                + '</button>';
        }
        var url = b.safeUrl(it.listing_url);
        var meta = [];
        if (it.keyword) meta.push("watch: " + b.escapeHTML(it.keyword));
        if (it.seller_location) meta.push(b.escapeHTML(it.seller_location));
        if (it.scraped_at) meta.push(b.fmtRelative(it.scraped_at));
        // data-listing-id on the OUTER card too so the ?focus=<id>
        // URL param can scrollIntoView() to the right row.
        return '<div class="activity-card" data-listing-id="' + lid + '">'
            + photo
            + scoreBlock
            + '<div class="body">'
            +   '<div class="title"><a href="' + b.escapeHTML(url) + '" target="_blank" rel="noopener">' + b.escapeHTML(it.title || "(untitled)") + '</a></div>'
            +   '<div class="meta">' + meta.join(" · ") + '</div>'
            + '</div>'
            + '<div class="price">' + b.fmtMoney(it.price) + '</div>'
            + '</div>';
    }

    /* Wire the score-block buttons added in the latest render to open
       the breakdown modal. Called after each load() (since load()
       replaces or appends to listEl.innerHTML, previous click
       listeners are blown away with their nodes). */
    function wireScoreClicks() {
        if (!listEl) return;
        listEl.querySelectorAll(".score-block-btn").forEach(function (btn) {
            // Idempotent: skip if we already wired this instance.
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

    /* Honor a ?focus=<listing_id> URL param by scrolling the matching
       row into view and briefly highlighting it. Used by the home-tab
       hot-deal card: clicking it lands on /activity?focus=<id>, and
       the user immediately sees "their" listing instead of having to
       hunt for it. */
    function focusListingFromUrl() {
        var params = new URLSearchParams(window.location.search || "");
        var fid = params.get("focus");
        if (!fid) return;
        // Wait one tick so the just-rendered DOM is laid out before we
        // measure scroll positions.
        setTimeout(function () {
            var card = listEl && listEl.querySelector(
                '.activity-card[data-listing-id="' + CSS.escape(fid) + '"]');
            if (!card) return;
            card.scrollIntoView({ behavior: "smooth", block: "center" });
            card.classList.add("activity-card-focused");
            setTimeout(function () {
                card.classList.remove("activity-card-focused");
            }, 2400);
        }, 80);
    }

    async function load(append) {
        if (loading) return;
        loading = true;
        if (!append) listEl.innerHTML = '<div class="muted">loading…</div>';
        try {
            var res = await b.apiGet(buildQuery(append));
            // Endpoint /api/dashboard/appraisal-feed returns
            // {"listings": [...]} — accept .items / .rows too so any
            // schema drift on either side stays harmless (this is the
            // same class of bug that bit tab_stats.js previously, in
            // the opposite direction). 2026-05-07.
            var items = res.listings || res.items || res.rows || [];
            if (!append) {
                if (!items.length) {
                    // Friendly empty state. The most common cause is a
                    // fresh install (database file gets replaced and
                    // historical listings vanish) — give the user a
                    // clear story for what to expect rather than a
                    // bare "no listings" line.
                    listEl.innerHTML =
                        '<div class="empty-state-card">' +
                            '<div class="empty-state-title">No scored listings yet.</div>' +
                            '<div class="muted" style="margin-top:6px;">' +
                                'Listings show up here as your saved searches poll Facebook ' +
                                'and the appraiser scores them. New searches typically ' +
                                'find their first hit within a few minutes.' +
                            '</div>' +
                            '<div style="margin-top:14px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap;">' +
                                '<a class="btn btn-primary" href="/test">Try a search now</a>' +
                                '<a class="btn btn-ghost" href="/watches">Manage saved searches</a>' +
                            '</div>' +
                            '<div class="muted" style="margin-top:14px;font-size:11px;">' +
                                'Did you just reinstall? Your local database was reset on install. ' +
                                'Past finds aren\'t recoverable, but new ones populate automatically.' +
                            '</div>' +
                        '</div>';
                    lastId = null;
                    return;
                }
                listEl.innerHTML = items.map(renderItem).join("");
                wireScoreClicks();
                focusListingFromUrl();
            } else {
                if (!items.length) {
                    b.toast("No more results.", "info");
                    return;
                }
                listEl.insertAdjacentHTML("beforeend", items.map(renderItem).join(""));
                wireScoreClicks();
            }
            lastId = items[items.length - 1].id;
        } catch (e) {
            if (!append) {
                listEl.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
            } else {
                b.toast(b.describeError(e), "error");
            }
        } finally {
            loading = false;
        }
    }

    document.getElementById("filter-apply").addEventListener("click", function () {
        lastId = null;
        load(false);
    });
    document.getElementById("filter-clear").addEventListener("click", function () {
        document.getElementById("filter-min").value = "";
        document.getElementById("filter-status").value = "all";
        lastId = null;
        load(false);
    });
    document.getElementById("load-more").addEventListener("click", function () {
        load(true);
    });

    document.addEventListener("DOMContentLoaded", function () { load(false); });
})();
