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

    // ----- error formatting ------------------------------------------

    function describeError(err) {
        if (!err) return "Unknown error.";
        if (typeof err === "string") return err;
        if (err.body && err.body.message) return err.body.message;
        if (err.body && err.body.error) return err.body.error;
        return err.message || "Unknown error.";
    }

    bullseye.toast = toast;
    bullseye.apiGet = apiGet;
    bullseye.apiPost = apiPost;

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
    bullseye.describeError = describeError;

    window.bullseye = bullseye;

    // ----- Referral modal --------------------------------------------
    //
    // Sidebar "Get a free month" button -> modal with Refer / Past
    // invites / Apply tabs. Wired here in shell.js so it works from
    // every tab. Tab switching is local (no server round-trip).

    // Global close function — exposed on window so the inline onclick=""
    // attributes in app_shell.html can call it as a last-resort fallback.
    // Idempotent: safe to call when modal is already closed.
    window.bxCloseReferralModal = function () {
        var modal = document.getElementById("referral-modal");
        if (!modal) return;
        modal.hidden = true;
        modal.style.display = "none";
        modal.classList.remove("is-open");
    };

    // Capture-phase document-level listener — runs BEFORE any other
    // handler in the DOM tree, so nothing downstream can swallow the
    // click. Three strikes on the X-button bug means we don't trust
    // bubble-phase delegation anymore.
    document.addEventListener("click", function (ev) {
        var hit = ev.target && ev.target.closest && ev.target.closest("[data-ref-close]");
        if (hit) window.bxCloseReferralModal();
    }, true);

    function setupReferral() {
        var openBtn = document.getElementById("open-referral");
        var modal = document.getElementById("referral-modal");
        if (!openBtn || !modal) return;

        // closeModal here delegates to the global so behavior is
        // consistent regardless of which path fires first.
        function closeModal() { window.bxCloseReferralModal(); }
        function openModal() {
            modal.hidden = false;
            modal.style.display = "";  // let stylesheet take over
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
