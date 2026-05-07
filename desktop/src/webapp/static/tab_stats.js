/* Stats tab — simplified port of dashboard.js. The original had ~1200
   lines of detail (live event tail, score histograms, pause/play, etc).
   This is the v1 of the new shell: status strip + funnel + per-watch
   table + external counters. We can add more later — the existing
   dashboard.html is still served at /dashboard during the smoke test. */
(function () {
    "use strict";
    var b = window.bullseye;

    function fmtCountdown(secs) {
        if (secs == null) return "—";
        if (secs < 0) secs = 0;
        if (secs < 60) return secs + "s";
        var m = Math.floor(secs / 60);
        var s = Math.floor(secs % 60);
        return m + "m " + (s < 10 ? "0" : "") + s + "s";
    }

    function fmtAgo(iso) {
        if (!iso) return "never";
        var t = Date.parse(iso);
        if (!t) return "never";
        var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
        if (s < 60) return s + "s ago";
        if (s < 3600) return Math.floor(s / 60) + "m " + (s % 60) + "s ago";
        if (s < 86400) return Math.floor(s / 3600) + "h ago";
        return Math.floor(s / 86400) + "d ago";
    }

    function diagnose(d) {
        // Quick rule-based diagnosis. Returns { msg, color }.
        if (!d.thread_alive) {
            return {
                msg: "Scheduler thread is NOT running. Restart the app — likely a crash during boot. Check %APPDATA%\\Bullseye\\bullseye.log for a stack trace.",
                color: "var(--bad)",
            };
        }
        if (d.kill_switch_active) {
            return {
                msg: "Kill switch is active — scheduler refused to boot. The cloud has flagged this app version as too old. Update from getbullseye.app.",
                color: "var(--bad)",
            };
        }
        if (d.active_watches === 0) {
            return {
                msg: "No active watches in the DB. Add one on the Watches tab.",
                color: "var(--muted)",
            };
        }
        if (!d.last_tick_at) {
            return {
                msg: "Scheduler is alive but no tick events yet. Wait ~30 seconds or click 'Poll all watches now' on Watches tab.",
                color: "var(--muted)",
            };
        }
        var tickAge = (Date.now() - Date.parse(d.last_tick_at)) / 1000;
        if (tickAge > 600) {
            return {
                msg: "Last tick was " + Math.floor(tickAge / 60) + " minutes ago — coordinator may be wedged. Restart the app.",
                color: "var(--bad)",
            };
        }
        if (d.cooldown_remaining_s && d.cooldown_remaining_s > 0) {
            return {
                msg: "FB rate-limit cooldown active (" + d.cooldown_remaining_s + "s remaining). Polls are paused; this is healthy backpressure.",
                color: "var(--accent)",
            };
        }
        if (!d.last_poll_at) {
            return {
                msg: "Scheduler is ticking but no polls yet. Either every tick is being gated (check recent events below), or the first poll is about to fire.",
                color: "var(--muted)",
            };
        }
        return {
            msg: "Scheduler is healthy. Last poll " + fmtAgo(d.last_poll_at) + ".",
            color: "var(--good, #5d7a4f)",
        };
    }

    /* -------- friendly event-row renderer ----------------------------
       The cloud serializes scheduler events as {at, type, detail}
       where `detail` is a JSON object. The previous UI showed a raw
       JSON dump, which the user (rightly) flagged as unreadable. This
       function renders each event as a one-liner with the most
       relevant fields surfaced and the rest hidden in a tooltip.
    ----------------------------------------------------------------- */
    function eventLabel(type) {
        switch (type) {
            case "poll":               return { text: "polled",      cls: "ev-poll" };
            case "coordinator_tick":   return { text: "tick",        cls: "ev-tick" };
            case "coordinator_idle":   return { text: "idle",        cls: "ev-idle" };
            case "rate_limited":       return { text: "rate-limit",  cls: "ev-warn" };
            case "circuit_open":       return { text: "circuit-open",cls: "ev-warn" };
            case "circuit_close":      return { text: "circuit-ok",  cls: "ev-good" };
            case "kill_switch":        return { text: "kill-switch", cls: "ev-bad" };
            case "scheduler_boot":     return { text: "boot",        cls: "ev-good" };
            case "appraisal":          return { text: "appraised",   cls: "ev-good" };
            case "alert_sent":         return { text: "alerted",     cls: "ev-good" };
            default:                   return { text: type || "—",   cls: "ev-default" };
        }
    }

    function formatEventDetail(type, d) {
        if (!d || typeof d !== "object") return "";
        if (type === "poll") {
            var parts = [];
            if (d.keyword) parts.push("\"" + d.keyword + "\"");
            if (d.raw_count != null) parts.push(d.raw_count + " found");
            if (d.new_count != null && d.new_count > 0) parts.push(d.new_count + " new");
            if (d.appraised_count != null && d.appraised_count > 0) parts.push(d.appraised_count + " scored");
            if (d.rejected_count != null && d.rejected_count > 0) parts.push(d.rejected_count + " rejected");
            if (d.distance_dropped != null && d.distance_dropped > 0) parts.push(d.distance_dropped + " too far");
            if (d.duration_ms != null) parts.push(d.duration_ms + "ms");
            return parts.join(" · ");
        }
        if (type === "coordinator_tick" || type === "coordinator_idle") {
            return d.reason ? "(" + d.reason + ")" : "";
        }
        if (type === "rate_limited") {
            return d.cooldown_s ? "cooldown " + d.cooldown_s + "s" : "";
        }
        // Fallback — short JSON, not the verbose dump.
        try {
            var keys = Object.keys(d);
            if (!keys.length) return "";
            var first = keys.slice(0, 3).map(function (k) { return k + "=" + JSON.stringify(d[k]); });
            return first.join(" · ");
        } catch (e) { return ""; }
    }

    function formatEventRow(e) {
        var lab = eventLabel(e.type);
        var detail = (typeof e.detail === "object" && e.detail !== null)
            ? formatEventDetail(e.type, e.detail)
            : (e.detail || "");
        var rawTitle = e.detail && typeof e.detail === "object"
            ? JSON.stringify(e.detail)
            : (e.detail || "");
        return '<div class="event-row" title="' + b.escapeHTML(rawTitle) + '">' +
            '<span class="event-when muted">' + b.escapeHTML(fmtAgo(e.at)) + '</span>' +
            '<span class="event-badge ' + lab.cls + '">' + b.escapeHTML(lab.text) + '</span>' +
            '<span class="event-detail">' + b.escapeHTML(detail) + '</span>' +
        '</div>';
    }

    async function loadSchedulerHealth() {
        try {
            var d = await b.apiGet("/api/scheduler/diagnose");
            document.getElementById("sh-thread").textContent =
                d.thread_alive ? "yes" : "NO";
            document.getElementById("sh-thread").style.color =
                d.thread_alive ? "" : "var(--bad)";
            document.getElementById("sh-tier").textContent = d.tier || "—";
            document.getElementById("sh-kill").textContent =
                d.kill_switch_active ? "ACTIVE (refusing boot)" : "ok";
            document.getElementById("sh-kill").style.color =
                d.kill_switch_active ? "var(--bad)" : "";
            document.getElementById("sh-watches").textContent =
                d.active_watches != null ? d.active_watches : "—";
            document.getElementById("sh-boot").textContent =
                fmtAgo(d.last_boot_at);
            document.getElementById("sh-tick").textContent =
                fmtAgo(d.last_tick_at);
            document.getElementById("sh-poll").textContent =
                fmtAgo(d.last_poll_at);
            document.getElementById("sh-cooldown").textContent =
                d.cooldown_remaining_s != null && d.cooldown_remaining_s > 0
                    ? d.cooldown_remaining_s + "s"
                    : "none";
            var sm = d.slow_start_mode
                ? "ON (interval " +
                  (d.slow_start_state && d.slow_start_state.min_interval_s) +
                  "s)"
                : "off";
            document.getElementById("sh-slow").textContent = sm;
            var dx = diagnose(d);
            var dEl = document.getElementById("sh-diagnosis");
            dEl.textContent = dx.msg;
            dEl.style.color = dx.color;
            dEl.style.background = "rgba(0,0,0,0.03)";

            // Events list — rendered as readable cards instead of a
            // raw-JSON table so non-developer users can scan it.
            var list = document.getElementById("sched-events-list");
            if (list) {
                var rows = (d.recent_events || []);
                if (!rows.length) {
                    list.innerHTML =
                        '<div class="muted" style="padding:16px;">No scheduler events yet.</div>';
                } else {
                    list.innerHTML = rows.map(formatEventRow).join("");
                }
            }
        } catch (e) {
            // Silent — placeholders stay
        }
    }

    async function loadSummary() {
        try {
            var s = await b.apiGet("/api/dashboard/summary");
            // Funnel
            var f = s.funnel_today || {};
            ["scraped", "rejected", "appraised", "over_threshold", "notified"].forEach(function (k) {
                var el = document.querySelector('[data-step="' + k + '"]');
                if (el) el.textContent = f[k] != null ? f[k] : "—";
            });

            // Counters
            document.getElementById("c-watches").textContent = s.active_watches != null ? s.active_watches : "—";
            var rates = s.rates || {};
            document.getElementById("c-polls").textContent = rates.polls_last_1h != null ? rates.polls_last_1h : "—";
            document.getElementById("c-rate").textContent = rates.rate_limits_last_24h != null ? rates.rate_limits_last_24h : "—";
            document.getElementById("c-errors").textContent = rates.pipeline_errors_24h != null ? rates.pipeline_errors_24h : "—";

            // External APIs
            var ext = s.external_apis || {};
            var mm = ext.minimax || {};
            var ebay = ext.ebay || {};
            document.getElementById("mm-today").textContent = mm.calls_today != null ? mm.calls_today : "—";
            document.getElementById("mm-budget").textContent = mm.daily_budget != null ? mm.daily_budget : "—";
            document.getElementById("ebay-today").textContent = ebay.calls_today != null ? ebay.calls_today : "—";

            // Poll timer
            var pt = s.poll_timer || {};
            var secs = pt.seconds_until_next;
            if (secs == null && pt.next_poll_iso) {
                secs = Math.round((Date.parse(pt.next_poll_iso) - Date.now()) / 1000);
            }
            document.getElementById("poll-countdown").textContent = fmtCountdown(secs);
            document.getElementById("poll-state").textContent = pt.state || (s.alive ? "alive" : "idle");
            document.getElementById("poll-detail").textContent = pt.detail || "";
        } catch (e) {
            // Silent — the page already shows placeholders.
        }
    }

    async function loadPerWatch() {
        var body = document.getElementById("per-watch-body");
        if (!body) return;
        try {
            var res = await b.apiGet("/api/dashboard/per-watch");
            var rows = res.rows || res.watches || [];
            if (!rows.length) {
                body.innerHTML = '<tr><td colspan="5" class="muted" style="padding:12px;">No watches yet.</td></tr>';
                return;
            }
            body.innerHTML = rows.map(function (r) {
                return '<tr style="border-top:1px solid var(--border);">'
                    + '<td style="padding:8px 12px;">' + b.escapeHTML(r.keyword || "—") + '</td>'
                    + '<td style="padding:8px 12px;">' + (r.polls_24h != null ? r.polls_24h : "—") + '</td>'
                    + '<td style="padding:8px 12px;">' + (r.avg_raw != null ? Number(r.avg_raw).toFixed(1) : "—") + '</td>'
                    + '<td style="padding:8px 12px;">' + (r.hits_24h != null ? r.hits_24h : "—") + '</td>'
                    + '<td style="padding:8px 12px;" class="muted">' + (r.last_scrape_iso ? b.fmtRelative(r.last_scrape_iso) : "—") + '</td>'
                    + '</tr>';
            }).join("");
        } catch (e) {
            body.innerHTML = '<tr><td colspan="5" class="muted" style="padding:12px;">' + b.escapeHTML(b.describeError(e)) + '</td></tr>';
        }
    }

    async function loadAppraisalFeed() {
        var body = document.getElementById("appraisal-feed-body");
        if (!body) return;
        try {
            var res = await b.apiGet("/api/dashboard/appraisal-feed?limit=20");
            // Endpoint returns { items: [...] } (legacy code also has
            // .rows in some paths). Live appraisals previously only
            // looked at .appraisals which was never set, so this feed
            // was permanently empty even with valid listings.
            // (Bug found 2026-05-07.)
            var rows = (res && (res.items || res.rows || res.appraisals)) || [];
            if (!rows.length) {
                body.innerHTML =
                    '<tr><td colspan="5" class="muted" style="padding:12px;">' +
                    'No appraisals yet — they’ll show up here as searches complete.' +
                    '</td></tr>';
                return;
            }
            body.innerHTML = rows.map(function (r) {
                var score = r.deal_score;
                var scoreCell = score == null
                    ? '<span class="muted">—</span>'
                    : '<span style="font-weight:700;color:' +
                      (score >= 80 ? "var(--good, #5d7a4f)"
                       : score >= 50 ? "var(--fg)"
                       : "var(--bad)") + ';">' + score + '</span>';
                var titleHtml = r.listing_url
                    ? '<a href="' + b.escapeHTML(r.listing_url) +
                      '" target="_blank" rel="noopener" style="color:var(--fg);">' +
                      b.escapeHTML(r.title || "(untitled)") + '</a>'
                    : b.escapeHTML(r.title || "(untitled)");
                var ask = r.asking_price != null
                    ? "$" + Number(r.asking_price).toLocaleString()
                    : "—";
                var med = r.comp_median != null
                    ? "$" + Number(r.comp_median).toLocaleString(undefined,
                          { maximumFractionDigits: 0 })
                    : "—";
                return '<tr style="border-bottom:1px solid rgba(0,0,0,0.05);">' +
                    '<td style="padding:8px 12px;white-space:nowrap;font-size:11px;color:var(--muted);">' +
                        (r.appraised_at ? b.escapeHTML(b.fmtRelative(r.appraised_at))
                                        : "—") + '</td>' +
                    '<td style="padding:8px 12px;white-space:nowrap;">' +
                        scoreCell + '</td>' +
                    '<td style="padding:8px 12px;">' + titleHtml + '</td>' +
                    '<td style="padding:8px 12px;white-space:nowrap;font-variant-numeric:tabular-nums;">' +
                        ask + '</td>' +
                    '<td style="padding:8px 12px;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--muted);">' +
                        med + '</td>' +
                    '</tr>';
            }).join("");
        } catch (e) {
            body.innerHTML =
                '<tr><td colspan="5" class="muted" style="padding:12px;">' +
                b.escapeHTML(b.describeError(e)) + '</td></tr>';
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        loadSummary();
        loadPerWatch();
        loadSchedulerHealth();
        loadAppraisalFeed();
        setInterval(loadSummary, 5000);
        setInterval(loadPerWatch, 30000);
        // Health panel refreshes faster — main use case is "is the
        // scheduler alive RIGHT NOW", and 3s makes the heartbeat
        // visibly tick when it works.
        setInterval(loadSchedulerHealth, 3000);
        // Appraisal feed refreshes every 5s — fast enough that the
        // user can see new listings appear as they're scored.
        setInterval(loadAppraisalFeed, 5000);
    });
})();
