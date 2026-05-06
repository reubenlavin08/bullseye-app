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

            // Events tail
            var body = document.getElementById("sched-events-body");
            if (body) {
                var rows = (d.recent_events || []);
                if (!rows.length) {
                    body.innerHTML =
                        '<tr><td colspan="3" class="muted" style="padding:12px;">' +
                        'No scheduler events yet. ' +
                        '</td></tr>';
                } else {
                    body.innerHTML = rows.map(function (e) {
                        var detail = e.detail
                            ? (typeof e.detail === "string"
                                ? e.detail
                                : JSON.stringify(e.detail))
                            : "";
                        return '<tr style="border-bottom:1px solid rgba(0,0,0,0.05);">' +
                            '<td style="padding:6px 12px;white-space:nowrap;font-family:var(--mono,monospace);font-size:11px;">' +
                                b.escapeHTML(fmtAgo(e.at)) + '</td>' +
                            '<td style="padding:6px 12px;white-space:nowrap;font-family:var(--mono,monospace);font-size:11px;">' +
                                b.escapeHTML(e.type) + '</td>' +
                            '<td style="padding:6px 12px;font-family:var(--mono,monospace);font-size:11px;color:var(--muted);">' +
                                b.escapeHTML(detail) + '</td>' +
                            '</tr>';
                    }).join("");
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

    document.addEventListener("DOMContentLoaded", function () {
        loadSummary();
        loadPerWatch();
        loadSchedulerHealth();
        setInterval(loadSummary, 5000);
        setInterval(loadPerWatch, 30000);
        // Health panel refreshes faster — main use case is "is the
        // scheduler alive RIGHT NOW", and 3s makes the heartbeat
        // visibly tick when it works.
        setInterval(loadSchedulerHealth, 3000);
    });
})();
