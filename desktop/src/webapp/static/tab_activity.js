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
        // The body now has THREE elements:
        //   - title link (opens FB)
        //   - meta line (keyword / location / time)
        //   - explicit "Score breakdown ↗" link, ONLY for scored rows
        //
        // The breakdown link was added 2026-05-07 because the user
        // reported "right now, it's unintuitive that you need to click
        // on the score number to see the actual breakdown of it." The
        // score badge stays clickable too (muscle memory) but now
        // there's a visible affordance.
        var hasScore = !rejected && !unscoreable;
        var breakdownLink = hasScore
            ? '<button type="button" class="activity-bd-link"'
              + ' data-listing-id="' + lid + '"'
              + '>Score breakdown <span aria-hidden="true">&rarr;</span></button>'
            : '';
        // data-listing-id on the OUTER card too so the ?focus=<id>
        // URL param can scrollIntoView() to the right row.
        return '<div class="activity-card" data-listing-id="' + lid + '">'
            + photo
            + scoreBlock
            + '<div class="body">'
            +   '<div class="title"><a href="' + b.escapeHTML(url) + '" target="_blank" rel="noopener">' + b.escapeHTML(it.title || "(untitled)") + '</a></div>'
            +   '<div class="meta">' + meta.join(" · ") + '</div>'
            +   breakdownLink
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
        // Both the score badge AND the new "Score breakdown →" link
        // open the modal — same behavior, two affordances.
        var triggers = listEl.querySelectorAll(".score-block-btn, .activity-bd-link");
        triggers.forEach(function (btn) {
            if (btn._bdWired) return;  // idempotent
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

    /* ---------- Polling countdown ---------------------------------- */
    /*                                                                  */
    /*  /api/dashboard/summary returns                                  */
    /*    { poll_timer: { next_poll_iso, seconds_until_next, ... } }    */
    /*  We resync every 10s (cheap GET) and tick a local 1s interval    */
    /*  in between so the countdown looks live without spamming the    */
    /*  endpoint. When the cadence info comes back we also stamp a     */
    /*  human-readable cadence sub-line ("polling every ~20s ·         */
    /*  N watches"). 2026-05-07.                                        */
    /* ---------------------------------------------------------------- */
    var pollState = { secondsLeft: null, cadenceText: "" };

    function fmtCountdown(secs) {
        if (secs == null) return "—";
        if (secs < 0) secs = 0;
        if (secs < 60) return secs + "s";
        var m = Math.floor(secs / 60);
        var s = Math.floor(secs % 60);
        return m + "m " + (s < 10 ? "0" : "") + s + "s";
    }

    function paintCountdown() {
        var cd = document.getElementById("poll-countdown");
        var sub = document.getElementById("poll-cadence-sub");
        var card = document.querySelector(".poll-timer-card");
        if (cd) cd.textContent = fmtCountdown(pollState.secondsLeft);
        if (sub && pollState.cadenceText) sub.textContent = pollState.cadenceText;
        if (card) {
            // .is-blocked turns the icon + sub-line red so a gated
            // scheduler is visually distinct from a healthy one.
            card.classList.toggle("is-blocked", !!pollState.blocked);
        }
    }

    async function refreshPollTimer() {
        try {
            // Two cheap fetches in parallel — the summary (next-poll
            // timer + cadence) and the scheduler status (gate state).
            // The status call exists specifically to surface "why
            // polling appears stuck": cooldown, circuit-breaker,
            // slow-start ramp. Added 2026-05-08 after a user-reported
            // "polling worked once then died" — turned out the FB
            // cooldown was silently gating every tick and the UI gave
            // the user no way to see that.
            var pair = await Promise.all([
                b.apiGet("/api/dashboard/summary"),
                b.apiGet("/api/scheduler/status").catch(function () { return null; }),
            ]);
            var s = pair[0] || {};
            var stat = (pair[1] && pair[1].status) || null;

            var pt = s.poll_timer || {};
            var secs = pt.seconds_until_next;
            if (secs == null && pt.next_poll_iso) {
                var dt = Date.parse(pt.next_poll_iso);
                if (!isNaN(dt)) secs = Math.max(0, Math.round((dt - Date.now()) / 1000));
            }
            if (secs != null) pollState.secondsLeft = secs;

            var n = s.active_watches || 0;
            var cadenceS = pt.cadence_seconds_per_watch
                || (pt.coordinator_tick_s && n
                    ? pt.coordinator_tick_s * n
                    : null);
            var cadenceLine;
            if (cadenceS) {
                var human = cadenceS < 60
                    ? cadenceS + "s"
                    : Math.round(cadenceS / 60) + " min";
                cadenceLine =
                    "Each watch polls about every " + human
                    + " · " + n + " active watch" + (n === 1 ? "" : "es");
            } else if (n) {
                cadenceLine = n + " active watch" + (n === 1 ? "" : "es");
            } else {
                cadenceLine = "No active watches yet.";
            }

            // If a gate is blocking, the explanation is more useful
            // than the cadence — show it in red on the sub-line and
            // override the countdown to show "—" so the user isn't
            // staring at a fake countdown that won't tick to a real
            // poll.
            if (stat && !stat.is_polling) {
                pollState.cadenceText = stat.explanation;
                pollState.blocked = true;
                if (stat.cooldown_remaining_s > 0) {
                    pollState.secondsLeft = stat.cooldown_remaining_s;
                }
            } else {
                pollState.cadenceText = cadenceLine;
                pollState.blocked = false;
            }
            paintCountdown();
        } catch (e) {
            // Auth-only endpoints; leave placeholders on 401.
        }
    }

    function tickCountdown() {
        if (pollState.secondsLeft == null) return;
        pollState.secondsLeft = Math.max(0, pollState.secondsLeft - 1);
        paintCountdown();
    }

    function wireSearchNowButton() {
        var btn = document.getElementById("activity-poll-now");
        if (!btn) return;
        btn.addEventListener("click", async function () {
            if (btn.disabled) return;
            var orig = btn.textContent;
            btn.disabled = true;
            btn.textContent = "Starting…";
            try {
                var r = await b.apiPost("/api/watches/poll-now", {});
                if (r && r.ok) {
                    var stat = r.status || null;
                    // The endpoint kicks the daemon thread regardless,
                    // but the gate state tells the user whether those
                    // polls will ACTUALLY hit Facebook or no-op behind
                    // the cooldown/circuit-breaker. Show the truthful
                    // outcome instead of a misleading "running ✓".
                    if (stat && !stat.is_polling) {
                        btn.textContent = "Blocked";
                        if (b.toast) {
                            b.toast(stat.explanation || "Polling currently gated.", "info");
                        }
                    } else if (r.started > 0) {
                        btn.textContent = "Running ✓";
                        if (b.toast) {
                            b.toast(
                                "Polling " + r.started + " watch(es). New finds appear "
                                + "in Recent finds within ~" + (r.started * 8) + "s.",
                                "success"
                            );
                        }
                    } else {
                        btn.textContent = "No active watches";
                        if (b.toast) b.toast("No active watches yet. Add one on Saved searches.", "info");
                    }
                    // Resync the countdown card immediately.
                    refreshPollTimer();
                } else if (r && r.error === "rate_limited") {
                    btn.textContent = "Wait a moment";
                    if (b.toast) b.toast(r.message || "Rate limited.", "info");
                }
            } catch (e) {
                btn.textContent = "Try again";
                if (b.toast) b.toast(b.describeError(e), "error");
            }
            setTimeout(function () {
                btn.disabled = false;
                btn.textContent = orig;
            }, 60000);
        });
    }

    /* ---------- Featured deal (top of page) ------------------------ */
    /*                                                                  */
    /*  Picks the highest-scoring listing in the last 7 days that's     */
    /*  still active (not rejected). This is the "screenshot for       */
    /*  Reddit" surface — big photo, big score, easy breakdown link.   */
    /*  Hidden when nothing qualifies.                                  */
    /* ---------------------------------------------------------------- */
    async function loadFeatured() {
        var section = document.getElementById("featured-section");
        if (!section) return;
        try {
            // Pull a generous slice of recent passed listings, then
            // sort client-side by score. Why client-side: the
            // server endpoint sorts by scraped_at, not score, so the
            // "newest 50" might not include the highest-scoring item.
            var s = await b.apiGet(
                "/api/dashboard/appraisal-feed?limit=50&filter=passed");
            var items = s.listings || s.items || s.rows || [];
            var cutoff = Date.now() - 7 * 24 * 3600 * 1000;
            var best = null;
            items.forEach(function (it) {
                if (!it || it.deal_score == null) return;
                if (it.rejected) return;
                var ts = it.scraped_at ? Date.parse(it.scraped_at) : 0;
                if (!ts || ts < cutoff) return;
                if (!best || it.deal_score > best.deal_score) best = it;
            });
            if (!best) {
                section.hidden = true;
                return;
            }
            section.hidden = false;

            var photoEl = document.getElementById("featured-photo");
            var scoreEl = document.getElementById("featured-score");
            var titleEl = document.getElementById("featured-title");
            var metaEl  = document.getElementById("featured-meta");
            var savEl   = document.getElementById("featured-savings");
            var bdBtn   = document.getElementById("featured-breakdown-btn");
            var fbLink  = document.getElementById("featured-fb-link");

            if (best.photo_url) {
                photoEl.style.backgroundImage = 'url(' + JSON.stringify(best.photo_url) + ')';
                photoEl.classList.remove("featured-photo-empty");
            } else {
                photoEl.style.backgroundImage = "";
                photoEl.classList.add("featured-photo-empty");
            }
            scoreEl.textContent = Math.round(best.deal_score);
            scoreEl.className = "featured-score " + b.scoreClass(best.deal_score);
            titleEl.textContent = best.title || "(untitled listing)";
            var metaParts = [];
            if (best.keyword) metaParts.push(b.escapeHTML(best.keyword));
            metaParts.push(b.fmtMoney(best.price));
            if (best.scraped_at) metaParts.push(b.fmtRelative(best.scraped_at));
            metaEl.innerHTML = metaParts.join(" · ");

            if (typeof best.fair_value === "number"
                && typeof best.price === "number"
                && best.fair_value > best.price) {
                savEl.innerHTML = "Save <strong>"
                    + b.fmtMoney(best.fair_value - best.price)
                    + "</strong> vs eBay sold-comp median of "
                    + b.fmtMoney(best.fair_value);
            } else {
                savEl.textContent = "";
            }

            bdBtn.onclick = function () { b.openBreakdownModal(String(best.id)); };
            fbLink.href = b.safeUrl(best.listing_url);
        } catch (e) {
            section.hidden = true;
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        load(false);
        loadFeatured();
        wireSearchNowButton();
        refreshPollTimer();
        // Tick the countdown locally every 1s (so the visible number
        // moves), and resync from the server every 10s so we stay
        // honest about what apscheduler thinks the next-poll time is.
        setInterval(tickCountdown, 1000);
        setInterval(refreshPollTimer, 10000);
        setInterval(loadFeatured, 60000);
    });
})();
