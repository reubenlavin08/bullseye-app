/* ----------------------------------------------------------------- *
 * shell.js — shared helpers loaded on every shell page.
 *
 * Exposes a tiny global `bullseye` namespace with the helpers each tab
 * file needs (apiGet/apiPost/toast/etc) so we don't end up with N
 * copies of the fetch+json+error boilerplate.
 *
 * Also runs the cheap auth-status check so a tab that opens after a
 * token has been cleared bounces to /auth instead of silently 401-ing
 * every API call.
 * ----------------------------------------------------------------- */
(function () {
    "use strict";

    var bullseye = window.bullseye || {};

    // ----- toasts -----------------------------------------------------

    function toast(message, kind) {
        kind = kind || "info";
        var stack = document.getElementById("toast-stack");
        if (!stack) return;
        var el = document.createElement("div");
        el.className = "toast is-" + kind;
        el.textContent = message;
        stack.appendChild(el);
        setTimeout(function () {
            el.style.transition = "opacity 200ms ease";
            el.style.opacity = "0";
            setTimeout(function () { el.remove(); }, 220);
        }, kind === "error" ? 5000 : 3000);
    }

    // ----- fetch helpers ---------------------------------------------

    async function apiGet(url) {
        var r = await fetch(url, { credentials: "same-origin" });
        var body = null;
        try { body = await r.json(); } catch (e) { /* ignore */ }
        if (r.status === 401) {
            // Auth dropped; bounce to sign-in.
            window.location.href = "/auth";
            throw new Error("login_required");
        }
        if (!r.ok) {
            var msg = (body && (body.message || body.error)) || ("HTTP " + r.status);
            var err = new Error(msg);
            err.status = r.status;
            err.body = body;
            throw err;
        }
        return body;
    }

    async function apiPost(url, payload, opts) {
        opts = opts || {};
        var method = opts.method || "POST";
        var r = await fetch(url, {
            method: method,
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: payload === undefined ? "{}" : JSON.stringify(payload || {}),
        });
        var body = null;
        try { body = await r.json(); } catch (e) { /* ignore */ }
        if (r.status === 401) {
            window.location.href = "/auth";
            throw new Error("login_required");
        }
        if (!r.ok) {
            var msg = (body && (body.message || body.error)) || ("HTTP " + r.status);
            var err = new Error(msg);
            err.status = r.status;
            err.body = body;
            throw err;
        }
        return body;
    }

    function apiPatch(url, payload) {
        return apiPost(url, payload, { method: "PATCH" });
    }
    function apiDelete(url) {
        return apiPost(url, undefined, { method: "DELETE" });
    }

    // ----- formatters -------------------------------------------------

    function fmtMoney(v) {
        if (v === null || v === undefined || v === "") return "—";
        var n = typeof v === "number" ? v : Number(v);
        if (!isFinite(n)) return "—";
        return "$" + Math.round(n).toLocaleString();
    }

    function fmtScore(v) {
        if (v === null || v === undefined) return "—";
        return String(Math.round(Number(v)));
    }

    function scoreClass(v) {
        if (v === null || v === undefined) return "score-low";
        var n = Number(v);
        if (n >= 70) return "score-high";
        if (n >= 50) return "score-mid";
        return "score-low";
    }

    function fmtRelative(iso) {
        if (!iso) return "";
        var t = Date.parse(iso);
        if (isNaN(t)) return "";
        var secs = (Date.now() - t) / 1000;
        if (secs < 60) return "just now";
        if (secs < 3600) return Math.floor(secs / 60) + "m ago";
        if (secs < 86400) return Math.floor(secs / 3600) + "h ago";
        if (secs < 86400 * 7) return Math.floor(secs / 86400) + "d ago";
        try {
            return new Date(t).toLocaleDateString();
        } catch (e) {
            return iso;
        }
    }

    function escapeHTML(s) {
        if (s === null || s === undefined) return "";
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    /* SAFE-URL helper — pre-validates that a string is a benign http/https
       URL before we interpolate it into an `href`. Listing URLs come from
       Facebook scrapes and could in principle contain `javascript:foo`,
       which would execute in the local pywebview origin (full DB access)
       when the user clicks the link. escapeHTML() blocks quote/angle
       injection but does NOT block scheme abuse. Use safeUrl() for ANY
       href that interpolates a server-supplied URL. Returns "#" for
       anything that isn't http(s) — link goes nowhere instead of running
       arbitrary JS. */
    function safeUrl(u) {
        if (!u) return "#";
        var s = String(u).trim();
        // Strip control chars + leading whitespace before scheme check.
        // Browsers tolerate `\tjavascript:foo` as a JS URL.
        s = s.replace(/[\x00-\x1f\x7f]/g, "");
        if (/^https?:\/\//i.test(s)) return s;
        // Allow internal absolute paths (start with /). Reject everything
        // else — `mailto:`, `data:`, `javascript:`, scheme-less, etc.
        if (s.charAt(0) === "/") return s;
        return "#";
    }

    // ----- error formatting ------------------------------------------

    function describeError(err) {
        if (!err) return "Unknown error.";
        if (typeof err === "string") return err;
        if (err.body && err.body.message) return err.body.message;
        if (err.body && err.body.error) return err.body.error;
        return err.message || "Unknown error.";
    }

    // ----- visibility-aware setInterval -------------------------------
    //
    // setInterval that auto-pauses while document.hidden and fires once
    // on restore so stale UI catches up. Returns a handle with stop()
    // for callers that need to tear down the timer (also removes the
    // entry from the shared visibility listener so it doesn't leak).
    var _sivTimers = [];
    var _sivListenerInstalled = false;
    function _ensureSivListener() {
        if (_sivListenerInstalled) return;
        _sivListenerInstalled = true;
        document.addEventListener("visibilitychange", function () {
            for (var i = 0; i < _sivTimers.length; i++) {
                var t = _sivTimers[i];
                if (document.hidden) {
                    if (t.handle != null) { clearInterval(t.handle); t.handle = null; }
                } else if (t.handle == null) {
                    t.handle = setInterval(t.fn, t.ms);
                    try { t.fn(); } catch (e) { /* swallow */ }
                }
            }
        });
    }
    function setIntervalVisible(fn, ms) {
        var entry = { fn: fn, ms: ms, handle: null };
        _sivTimers.push(entry);
        _ensureSivListener();
        if (!document.hidden) entry.handle = setInterval(fn, ms);
        return {
            stop: function () {
                if (entry.handle != null) { clearInterval(entry.handle); entry.handle = null; }
                var ix = _sivTimers.indexOf(entry);
                if (ix >= 0) _sivTimers.splice(ix, 1);
            }
        };
    }

    bullseye.toast = toast;
    bullseye.apiGet = apiGet;
    bullseye.apiPost = apiPost;
    bullseye.escapeHTML = escapeHTML;
    bullseye.safeUrl = safeUrl;
    bullseye.fmtMoney = fmtMoney;
    bullseye.fmtScore = fmtScore;
    bullseye.scoreClass = scoreClass;
    bullseye.fmtRelative = fmtRelative;
    bullseye.describeError = describeError;
    bullseye.setIntervalVisible = setIntervalVisible;

    // ----- score-breakdown modal (global) ---------------------------------
    //
    // Lives in shell.js (not a tab-specific JS file) so any tab that
    // renders listings — Home's "Top finds" feed, /activity's full
    // chronological list, the hot-deal right-rail card — can call
    // `b.openBreakdownModal(listingId)` and get the same UX.
    //
    // The modal markup is in app_shell.html (rendered on every logged-
    // in page). Inline onclick handlers on the X + backdrop handle
    // closing, plus an Escape-key listener registered in app_shell.html.
    //
    // Wired up 2026-05-07 after the user reported "nowhere on my
    // listings can I click to see the score breakdown and see their
    // comps." Was originally tab_home-only; promoted up here so
    // /activity gets the same affordance.

    function fmtPct(v) {
        if (v == null || isNaN(v)) return "—";
        var n = Number(v);
        if (n <= 1) n = n * 100;
        return n.toFixed(0) + "%";
    }

    async function openBreakdownModal(listingId) {
        var modal = document.getElementById("breakdown-modal");
        var body = document.getElementById("bd-modal-body");
        if (!modal || !body) return;
        modal.classList.add("is-open");
        modal.setAttribute("aria-hidden", "false");
        body.innerHTML = '<div class="muted" style="padding:48px 24px;text-align:center;">Loading…</div>';
        try {
            var d = await apiGet("/api/dashboard/breakdown/" + encodeURIComponent(listingId));
            renderBreakdown(d, body);
            var compTerm = d && d.comp && d.comp.search_term;
            var compSource = d && d.comp && d.comp.source;
            var canonicalMedian = d && d.comp ? d.comp.median : null;
            if (compTerm) {
                renderBreakdownComps(body, compTerm, compSource, canonicalMedian);
            } else {
                var target = body.querySelector("#bd-comps-content");
                if (target) {
                    target.textContent =
                        "No comp search term recorded for this listing " +
                        "(usually means it was scored before the comp " +
                        "cache had a hit). Re-appraise to refresh.";
                }
            }
        } catch (e) {
            if (e && e.status === 403) {
                body.innerHTML = renderUpgradePitch();
            } else {
                body.innerHTML =
                    '<div class="muted" style="padding:32px;">Could not load breakdown: ' +
                    escapeHTML(describeError(e)) + '</div>';
            }
        }
    }

    function renderUpgradePitch() {
        return ''
            + '<div class="bd-upgrade">'
            +   '<div class="bd-upgrade-icon-wrap" aria-hidden="true">'
            +     '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            +       '<rect x="4" y="11" width="16" height="10" rx="2"></rect>'
            +       '<path d="M8 11V7a4 4 0 0 1 8 0v4"></path>'
            +     '</svg>'
            +   '</div>'
            +   '<p class="bd-upgrade-kicker">Pro feature</p>'
            +   '<h2 class="bd-upgrade-title">See how every score was built</h2>'
            +   '<p class="bd-upgrade-sub">'
            +     "Bullseye's scoring is deterministic — every number is backed "
            +     "by real eBay sold-comps. Pro unlocks the full receipt."
            +   '</p>'
            +   '<ul class="bd-upgrade-features">'
            +     '<li>All eBay sold-comp listings with prices and links</li>'
            +     '<li>Confidence band, sample size, and IQR breakdown</li>'
            +     '<li>Condition-signal adjustments (battery, screen, etc.)</li>'
            +     '<li>Honesty-cap reasons whenever a score was held back</li>'
            +     '<li>Per-listing percentile rank within its keyword</li>'
            +   '</ul>'
            +   '<div class="bd-upgrade-cta">'
            +     '<a class="bd-upgrade-btn-primary" href="/upgrade">Start 7-day free trial</a>'
            +     '<a class="bd-upgrade-btn-secondary" href="/upgrade">See pricing →</a>'
            +   '</div>'
            +   '<div class="bd-upgrade-fineprint">No credit card · Cancel any time</div>'
            + '</div>';
    }

    /* Advertising-worthy modal layout, 2026-05-07.
       Top:  [photo column]  [hero column with title + huge score + comp summary]
       Mid:  Savings strip (big "Save $X" callout when applicable)
       Body: Stats grid (percentile rank, confidence, condition adj, etc.)
       Comps: full sold-comp rows pulled from /api/comps
       Footer: View on Marketplace + share-friendly URL */
    function renderBreakdown(d, body) {
        var bd = d.breakdown || {};
        if (!bd || typeof bd !== "object") bd = {};
        var score = d.deal_score;
        var fair = d.fair_value;
        var ask = d.price;
        var savings = (typeof fair === "number" && typeof ask === "number")
            ? Math.max(0, fair - ask) : null;
        var savingsPct = (savings != null && fair > 0)
            ? Math.round((savings / fair) * 100) : null;
        var pctRank = bd.percentile_rank;
        var conf = bd.confidence_label || "—";
        var pm = bd.confidence_pm;
        var condAdj = bd.condition_adj != null ? bd.condition_adj : bd.condition_adjustment;
        var capReason = bd.cap_reason || bd.honesty_cap_reason;
        var compN = d.comp ? d.comp.sample_size : null;
        var compMedian = d.comp ? d.comp.median : null;

        // Photo column. Uses a real <img> element (NOT background-image
        // via inline style) because FB CDN URLs contain double quotes
        // when JSON.stringify'd, and embedding "..." inside a style="..."
        // attribute breaks HTML parsing — the attribute closes early
        // and the URL leaks into the DOM as junk attributes. The
        // leaderboard's lb-photo pattern handles the same URLs fine
        // with <img>, so we use the same pattern here. Object-fit:
        // cover on the image keeps the cropping behavior we wanted
        // from background-size: cover.
        var photoBody;
        var photoEmpty = "";
        if (d.photo_url) {
            photoBody = '<img class="bd-photo-img" src="'
                + escapeHTML(d.photo_url)
                + '" alt="" loading="lazy" referrerpolicy="no-referrer">';
        } else {
            photoEmpty = ' bd-photo-empty';
            photoBody = '<span>no photo</span>';
        }

        // Hero column: Marketplace listing title (Georgia serif),
        // giant score (matches the Featured Find on /activity), then
        // a one-line "$X · save $Y · vs eBay median $Z" summary.
        var compSummary = "";
        if (compN != null && compMedian != null) {
            compSummary =
                '<span class="bd-hero-comp-sum">'
                + compN + ' eBay sold comps · median '
                + fmtMoney(compMedian) + '</span>';
        }

        // Savings callout is the screenshot-worthy headline. Hidden
        // when there's no real savings (priced at or above fair value).
        // Sub-line shows fair_value (NOT median — fair_value is the
        // discounted estimate we benchmark asking against; mislabeling
        // it "median" caused a 3-different-numbers bug in the modal).
        var savingsBlock = "";
        if (savings != null && savings >= 1) {
            savingsBlock =
                '<div class="bd-savings-strip">'
                + '<div class="bd-savings-amount">'
                +   'Save ' + fmtMoney(savings)
                +   (savingsPct != null
                        ? ' <span class="bd-savings-pct">(' + savingsPct + '% under)</span>'
                        : "")
                + '</div>'
                + '<div class="bd-savings-sub">'
                +   'Asking ' + fmtMoney(ask)
                +   ' · fair value ' + fmtMoney(fair)
                +   (compMedian != null
                        ? ' · sold-comp median ' + fmtMoney(compMedian)
                        : "")
                + '</div>'
                + '</div>';
        }

        // Stats grid — same data as before but in a grid layout instead
        // of a two-column dl. More visual at the smaller card sizes.
        var stats = [];
        stats.push({
            label: "Percentile rank",
            value: fmtPct(pctRank),
            sub: pctRank != null
                ? "cheaper than " + fmtPct(1 - pctRank) + " of comps"
                : "",
        });
        stats.push({
            label: "Confidence",
            value: conf || "—",
            sub: pm != null ? "±$" + Math.round(pm) : "",
        });
        if (condAdj != null && Number(condAdj) !== 0) {
            var sign = Number(condAdj) > 0 ? "+" : "";
            stats.push({
                label: "Condition adj.",
                value: sign + Math.round(condAdj),
                sub: "applied to raw score",
            });
        }
        if (capReason) {
            stats.push({
                label: "Honesty cap",
                value: escapeHTML(String(capReason)),
                sub: "score capped",
            });
        }
        if (d.listed_at) {
            stats.push({
                label: "Listed on FB",
                value: fmtRelative(d.listed_at),
                sub: new Date(Date.parse(d.listed_at)).toLocaleDateString(),
            });
        }
        if (d.appraised_at) {
            stats.push({
                label: "Appraised",
                value: fmtRelative(d.appraised_at),
                sub: new Date(Date.parse(d.appraised_at)).toLocaleString(),
            });
        }

        var statsHtml = '<div class="bd-stats-grid">';
        stats.forEach(function (s) {
            statsHtml += '<div class="bd-stat">'
                + '<div class="bd-stat-label">' + escapeHTML(s.label) + '</div>'
                + '<div class="bd-stat-value">' + s.value + '</div>'
                + (s.sub
                    ? '<div class="bd-stat-sub muted">' + s.sub + '</div>'
                    : '')
                + '</div>';
        });
        statsHtml += '</div>';

        // Footer actions: open on Marketplace (system browser) +
        // location/keyword chips that double as listing context.
        var locChip = d.seller_location
            ? '<span class="bd-chip">' + escapeHTML(d.seller_location) + '</span>'
            : '';
        var kwChip = d.keyword
            ? '<span class="bd-chip">' + escapeHTML(d.keyword) + '</span>'
            : '';
        var distChip = (d.distance_km != null)
            ? '<span class="bd-chip">' + Math.round(d.distance_km) + ' km away</span>'
            : '';

        var fbUrl = d.listing_url ? safeUrl(d.listing_url) : "";
        var fbLink = fbUrl
            ? '<a href="' + escapeHTML(fbUrl) + '" target="_blank" rel="noopener" '
              + 'class="btn btn-primary bd-fb-link">View on Marketplace &rarr;</a>'
            : '';

        body.innerHTML =
            '<div class="bd-hero">'
            +   '<div class="bd-photo' + photoEmpty + '">'
            +     photoBody
            +   '</div>'
            +   '<div class="bd-hero-body">'
            +     '<div class="bd-hero-chips">' + kwChip + locChip + distChip + '</div>'
            +     '<h3 class="bd-hero-title">' + escapeHTML(d.title || "(untitled listing)") + '</h3>'
            +     '<div class="bd-hero-score-row">'
            +       '<span class="bd-hero-score ' + scoreClass(score) + '">' + fmtScore(score) + '</span>'
            +       '<span class="bd-hero-score-meta muted">/ 100</span>'
            +     '</div>'
            +     (compSummary ? '<div class="bd-hero-comp-line">' + compSummary + '</div>' : '')
            +   '</div>'
            + '</div>'
            + savingsBlock
            + statsHtml
            + '<div class="bd-comps-section">'
            +   '<h3 class="bd-section-h3">eBay sold comps</h3>'
            +   '<div id="bd-comps-content" class="muted">Loading comps…</div>'
            + '</div>'
            + (fbLink
                ? '<div class="bd-footer">' + fbLink + '</div>'
                : '');
    }

    async function renderBreakdownComps(body, term, source, canonicalMedian) {
        var target = body.querySelector("#bd-comps-content");
        if (!target) return;
        try {
            var url = "/api/comps?term=" + encodeURIComponent(term)
                    + "&source=" + encodeURIComponent(source || "ebay");
            var data = await apiGet(url);
            if (!data || !data.rows || !data.rows.length) {
                target.textContent = 'No cached comps for "' + term + '". ' +
                    "They may have expired (12h TTL); re-appraise to refresh.";
                return;
            }
            var max = data.max || 1;
            // Prefer the appraisal-time median (passed from the parent
            // breakdown payload) over the just-fetched recompute, so the
            // modal shows ONE consistent median across hero summary,
            // savings strip, and comps section. Fall back to the local
            // recompute if the parent didn't provide one.
            var displayMedian = canonicalMedian != null ? canonicalMedian : data.median;
            var median = displayMedian || 0;
            var html =
                '<div class="muted bd-comps-summary">'
                + data.sample_size + ' comp(s) · median '
                + fmtMoney(displayMedian) + ' · range '
                + fmtMoney(data.min) + ' – ' + fmtMoney(data.max)
                + '</div><ul class="bd-comps-list">';
            data.rows.forEach(function (row) {
                var pct = ((row.price / max) * 100).toFixed(1);
                var nearMedian = Math.abs(row.price - median) / median < 0.15;
                var safeu = row.listing_url ? safeUrl(row.listing_url) : "";
                var open = safeu
                    ? '<a class="bd-comp-row' + (nearMedian ? ' bd-near-median' : '')
                      + '" href="' + escapeHTML(safeu)
                      + '" target="_blank" rel="noopener">'
                    : '<div class="bd-comp-row' + (nearMedian ? ' bd-near-median' : '') + '">';
                var close = safeu ? '</a>' : '</div>';
                html += open
                    + '<span class="bd-comp-bar" style="width:' + pct + '%;"></span>'
                    + '<span class="bd-comp-price">' + fmtMoney(row.price) + '</span>'
                    + '<span class="bd-comp-title">' + escapeHTML(row.title || "(no title)") + '</span>'
                    + close;
            });
            html += '</ul>';
            target.innerHTML = html;
        } catch (e) {
            target.textContent = "Could not load comps: " + describeError(e);
        }
    }

    bullseye.openBreakdownModal = openBreakdownModal;

    // ----- confirm() replacement ---------------------------------------
    //
    // pywebview wraps Chromium and falls back to the native browser
    // confirm()/alert() dialogs, which render as a black "127.0.0.1
    // says" box that screams "this is just a webview". We replace it
    // with a custom modal that matches the app's visual language and
    // returns a Promise<boolean> just like the call sites need.
    //
    // Usage:
    //     if (!await b.confirm("Delete this watch?")) return;
    //     if (!await b.confirm("...", { ok: "Delete", danger: true })) return;
    function customConfirm(message, opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            var existing = document.getElementById("bullseye-confirm-modal");
            if (existing) existing.remove();

            var wrap = document.createElement("div");
            wrap.id = "bullseye-confirm-modal";
            wrap.className = "bx-confirm";
            wrap.setAttribute("role", "dialog");
            wrap.setAttribute("aria-modal", "true");
            var okLabel = opts.ok || "Confirm";
            var cancelLabel = opts.cancel || "Cancel";
            var dangerCls = opts.danger ? " btn-danger" : " btn-primary";
            wrap.innerHTML =
                '<div class="bx-confirm-backdrop"></div>' +
                '<div class="bx-confirm-card">' +
                '  <div class="bx-confirm-msg"></div>' +
                '  <div class="bx-confirm-actions">' +
                '    <button type="button" class="btn bx-cancel">' +
                       cancelLabel + '</button>' +
                '    <button type="button" class="btn ' + dangerCls.trim() +
                '            bx-ok">' + okLabel + '</button>' +
                '  </div>' +
                '</div>';
            // textContent prevents HTML injection from caller-supplied
            // strings (the message is the only dynamic field).
            wrap.querySelector(".bx-confirm-msg").textContent = String(message);
            document.body.appendChild(wrap);

            function close(result) {
                document.removeEventListener("keydown", onKey);
                wrap.remove();
                resolve(result);
            }
            function onKey(e) {
                if (e.key === "Escape") close(false);
                if (e.key === "Enter")  close(true);
            }
            wrap.querySelector(".bx-cancel").addEventListener("click", function () { close(false); });
            wrap.querySelector(".bx-ok").addEventListener("click", function () { close(true); });
            wrap.querySelector(".bx-confirm-backdrop").addEventListener("click", function () { close(false); });
            document.addEventListener("keydown", onKey);
            // Focus the OK button so Enter works without a click first.
            setTimeout(function () { wrap.querySelector(".bx-ok").focus(); }, 0);
        });
    }
    bullseye.confirm = customConfirm;
    bullseye.apiPatch = apiPatch;
    bullseye.apiDelete = apiDelete;
    bullseye.fmtMoney = fmtMoney;
    bullseye.fmtScore = fmtScore;
    bullseye.scoreClass = scoreClass;
    bullseye.fmtRelative = fmtRelative;
    bullseye.escapeHTML = escapeHTML;
    bullseye.safeUrl = safeUrl;
    bullseye.describeError = describeError;

    window.bullseye = bullseye;

    // ----- Referral modal --------------------------------------------
    //
    // Sidebar "Get a free month" button -> modal with Refer / Past
    // invites / Apply tabs. Wired here in shell.js so it works from
    // every tab. Tab switching is local (no server round-trip).

    // Global close function — exposed on window so any caller can
    // close the referral modal by class manipulation. Single source
    // of truth for visibility is the `.is-open` class on `.cl-modal`.
    window.bxCloseReferralModal = function () {
        var modal = document.getElementById("referral-modal");
        if (!modal) return;
        modal.classList.remove("is-open");
    };

    // Capture-phase document-level listener — runs BEFORE any other
    // handler in the DOM tree, so nothing downstream can swallow the
    // click. The inline <script> at the end of the modal HTML attaches
    // a redundant addEventListener handler too — belt and suspenders.
    document.addEventListener("click", function (ev) {
        var hit = ev.target && ev.target.closest && ev.target.closest("[data-ref-close]");
        if (hit) window.bxCloseReferralModal();
    }, true);

    function setupReferral() {
        var openBtn = document.getElementById("open-referral");
        var modal = document.getElementById("referral-modal");
        if (!openBtn || !modal) return;

        function closeModal() { window.bxCloseReferralModal(); }
        function openModal() {
            modal.classList.add("is-open");
            loadInfo();
        }

        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && !modal.hidden) closeModal();
        });
        openBtn.addEventListener("click", openModal);

        var tabs = modal.querySelectorAll(".ref-tab");
        var panes = modal.querySelectorAll(".ref-pane");
        tabs.forEach(function (tab) {
            tab.addEventListener("click", function () {
                var name = tab.getAttribute("data-ref-tab");
                tabs.forEach(function (t) { t.classList.toggle("is-active", t === tab); });
                panes.forEach(function (p) {
                    var match = p.getAttribute("data-ref-pane") === name;
                    p.hidden = !match;
                    p.classList.toggle("is-active", match);
                });
            });
        });

        var copyBtn = document.getElementById("ref-link-copy");
        var linkInput = document.getElementById("ref-link-input");
        copyBtn.addEventListener("click", function () {
            try {
                linkInput.select();
                document.execCommand("copy");
                copyBtn.textContent = "Copied";
                setTimeout(function () { copyBtn.textContent = "Copy"; }, 1500);
            } catch (e) { /* ignore */ }
        });

        var applyBtn = document.getElementById("ref-apply-btn");
        var applyInput = document.getElementById("ref-apply-input");
        var applyStatus = document.getElementById("ref-apply-status");
        applyBtn.addEventListener("click", async function () {
            var code = (applyInput.value || "").trim().toUpperCase();
            if (!code) {
                applyStatus.textContent = "Enter a code first.";
                return;
            }
            applyBtn.disabled = true;
            applyStatus.textContent = "Applying...";
            try {
                await window.bullseye.apiPost("/api/referral/claim", { code: code });
                applyStatus.style.color = "var(--accent-2)";
                applyStatus.textContent = "Applied. You'll get a free month when your next invoice runs.";
                applyInput.value = "";
            } catch (e) {
                applyStatus.style.color = "var(--bad)";
                applyStatus.textContent = window.bullseye.describeError(e);
            } finally {
                applyBtn.disabled = false;
            }
        });

        async function loadInfo() {
            try {
                var info = await window.bullseye.apiGet("/api/referral/info");
                linkInput.value = info.link || "";
                document.getElementById("ref-earned").textContent = info.earned || 0;
                document.getElementById("ref-pending").textContent = info.pending || 0;
                var inviteList = document.getElementById("ref-invite-list");
                var inviteCount = document.getElementById("ref-invite-count");
                var refs = info.referrals || [];
                inviteCount.textContent = "(" + refs.length + ")";
                if (!refs.length) {
                    inviteList.innerHTML = '<span class="muted">No invites yet. Share your link.</span>';
                } else {
                    inviteList.innerHTML = refs.map(function (r) {
                        var statusClass = r.status === "earned" ? "score-good"
                            : r.status === "voided" ? "score-meh" : "score-ok";
                        return '<div class="ref-invite-row">'
                            + '<span class="muted">' + window.bullseye.fmtRelative(r.created_at) + '</span>'
                            + '<span class="score ' + statusClass + '" style="padding:2px 8px;font-size:11px;">'
                            +   window.bullseye.escapeHTML(r.status)
                            + '</span>'
                            + '</div>';
                    }).join("");
                }
            } catch (e) {
                linkInput.value = "Could not load: " + window.bullseye.describeError(e);
            }
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setupReferral);
    } else {
        setupReferral();
    }

    // ----- License tier auto-refresh -----------------------------------
    //
    // The sidebar tier badge ("FREE" / "PRO" / "TRIAL ends in N days")
    // is rendered server-side via _shell_context(). When the user
    // activates a trial elsewhere (e.g. via the website checkout flow,
    // or Stripe Customer Portal in another tab), the desktop app
    // doesn't notice until the next page load. That's confusing —
    // they paid (or trialed) but the app insists they're FREE.
    //
    // Fix: hit /api/license/refresh on visibilitychange (when the user
    // tabs back into the app) and every 90 seconds while open. If the
    // tier we get back differs from the tier we rendered with, do a
    // full reload so _shell_context() re-runs and the sidebar updates.
    function setupLicenseRefresh() {
        var pill = document.querySelector(".sidebar-tier, .sidebar-trial");
        // Read the rendered tier off a data attr if present, else
        // assume the page was rendered for whatever tier the server
        // currently knows. We compare by string equality.
        var renderedTier = (pill && pill.getAttribute("data-tier"))
            || (document.body.getAttribute("data-tier"))
            || "";

        var refreshing = false;
        async function refresh() {
            if (refreshing) return;
            refreshing = true;
            try {
                var r = await fetch("/api/license/refresh", {
                    method: "POST",
                    credentials: "same-origin",
                    headers: { "Content-Type": "application/json" },
                    body: "{}",
                });
                if (!r.ok) return;
                var data = await r.json();
                var fresh = (data && data.license && data.license.tier) || "";
                if (renderedTier && fresh && fresh !== renderedTier) {
                    // Tier changed under us — full reload so the
                    // sidebar + every server-rendered surface picks
                    // up the new value.
                    window.location.reload();
                }
            } catch (e) { /* offline / transient — try again later */ }
            finally { refreshing = false; }
        }

        // Trigger on tab re-focus (most common: user opened Stripe in
        // another window, came back to the app).
        document.addEventListener("visibilitychange", function () {
            if (!document.hidden) refresh();
        });
        window.addEventListener("focus", refresh);
        // Also poll while the app is open in case nothing else fires
        // (e.g. user activated trial via /api/trial/start in this same
        // tab — the upgrade page reloads itself, but other tabs need
        // this).
        setInterval(refresh, 90000);
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setupLicenseRefresh);
    } else {
        setupLicenseRefresh();
    }
})();
