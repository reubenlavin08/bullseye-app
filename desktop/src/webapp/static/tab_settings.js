/* Settings tab — location form, telemetry toggle, account ops. */
(function () {
    "use strict";
    var b = window.bullseye;

    // ----- toggle helper ---------------------------------------------
    function setToggle(toggleEl, on) {
        toggleEl.classList.toggle("is-on", !!on);
        var cb = toggleEl.querySelector("input[type='checkbox']");
        if (cb) cb.checked = !!on;
    }

    // ----- location ---------------------------------------------------

    var searchEl = document.getElementById("loc-search");
    var suggEl = document.getElementById("loc-suggestions");
    var labelEl = document.getElementById("loc-label");
    var latEl = document.getElementById("loc-lat");
    var lngEl = document.getElementById("loc-lng");
    var saveBtn = document.getElementById("loc-save");
    var currentLabel = document.getElementById("loc-current-label");
    var locForm = document.getElementById("location-form");

    var debounceTimer = null;
    searchEl.addEventListener("input", function () {
        var q = searchEl.value.trim();
        if (debounceTimer) clearTimeout(debounceTimer);
        if (q.length < 3) {
            suggEl.innerHTML = "";
            return;
        }
        debounceTimer = setTimeout(async function () {
            try {
                var res = await b.apiGet("/api/geocode?q=" + encodeURIComponent(q));
                var rows = (res.results || []).slice(0, 5);
                if (!rows.length) {
                    suggEl.textContent = "No matches.";
                    return;
                }
                suggEl.innerHTML = rows.map(function (r, i) {
                    return '<div style="padding:4px 0;cursor:pointer;border-bottom:1px solid var(--border);" data-idx="' + i + '">'
                        + b.escapeHTML(r.label)
                        + '</div>';
                }).join("");
                Array.from(suggEl.children).forEach(function (child, i) {
                    child.addEventListener("click", function () {
                        var pick = rows[i];
                        labelEl.value = pick.label;
                        latEl.value = pick.lat;
                        lngEl.value = pick.lng;
                        searchEl.value = pick.label;
                        suggEl.innerHTML = "";
                        saveBtn.disabled = false;
                    });
                });
            } catch (e) {
                suggEl.textContent = b.describeError(e);
            }
        }, 300);
    });

    locForm.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        if (!latEl.value || !lngEl.value) {
            b.toast("Pick a result from the list first.", "error");
            return;
        }
        try {
            await b.apiPost("/api/settings", {
                home_label: labelEl.value,
                home_latitude: Number(latEl.value),
                home_longitude: Number(lngEl.value),
            });
            b.toast("Location saved.", "success");
            currentLabel.textContent = labelEl.value;
        } catch (e) {
            b.toast(b.describeError(e), "error");
        }
    });

    // ----- initial settings load -------------------------------------
    //
    // Pulls every toggle's persisted state in one call so the UI is in
    // sync on first paint. Each toggle reflects the inverse of its
    // *opt-out* form for telemetry (UI shows "telemetry on" = NOT
    // opt-out). Notification toggles are direct ("milestones on" = the
    // flag is true).

    async function loadSettings() {
        try {
            var s = await b.apiGet("/api/settings");
            if (s.home_label) currentLabel.textContent = s.home_label;
            setToggle(document.getElementById("toggle-telemetry"), !s.telemetry_opt_out);
            // Account-wide master toggles (added 2026-05-06)
            var emailToggle = document.getElementById("toggle-email-global");
            if (emailToggle) {
                setToggle(emailToggle, !!s.notif_email_global);
            }
            var deskToggle = document.getElementById("toggle-desktop-global");
            if (deskToggle) {
                setToggle(deskToggle, !!s.notif_desktop_global);
            }
            setToggle(document.getElementById("toggle-milestones"), !!s.notif_milestones);
            setToggle(document.getElementById("toggle-first-deal"), !!s.notif_first_deal);
            setToggle(document.getElementById("toggle-kill-switch"), !!s.notif_kill_switch_banner);
        } catch (e) {
            // Settings load is non-critical — silent.
        }
    }

    // ----- telemetry toggle (inverse: UI on = opt_out false) ---------

    document.getElementById("toggle-telemetry").addEventListener("click", async function () {
        var t = this;
        var next = !t.classList.contains("is-on");
        setToggle(t, next);
        try {
            await b.apiPost("/api/settings", { telemetry_opt_out: !next });
            b.toast("Telemetry " + (next ? "enabled" : "disabled") + ".", "success");
        } catch (e) {
            // Revert on failure.
            setToggle(t, !next);
            b.toast(b.describeError(e), "error");
        }
    });

    // ----- generic notification toggles ------------------------------
    //
    // Each toggle has data-flag matching its server-side column name.
    // Optimistic update: flip UI immediately, revert on error. One
    // handler covers all three so adding a new toggle just means
    // adding the row + data-flag in tab_settings.html.

    function wireNotifToggle(elId, label) {
        var el = document.getElementById(elId);
        if (!el) return;
        // Disabled toggles (free-tier email, etc.) shouldn't be
        // clickable. The CSS class `is-disabled` is the visual cue;
        // we also block the click handler so a stray bubble doesn't
        // round-trip a 403 to the server.
        var flag = el.getAttribute("data-flag");
        el.addEventListener("click", async function (ev) {
            if (el.classList.contains("is-disabled")) {
                ev.preventDefault();
                ev.stopPropagation();
                b.toast("Email notifications are a Pro feature. Start the free trial in Upgrade.", "info");
                return;
            }
            var next = !el.classList.contains("is-on");
            setToggle(el, next);
            var payload = {};
            payload[flag] = next;
            try {
                await b.apiPost("/api/settings", payload);
                b.toast(label + " " + (next ? "on" : "off") + ".", "success");
            } catch (e) {
                setToggle(el, !next);
                b.toast(b.describeError(e), "error");
            }
        });
    }

    // Master account-wide toggles (added 2026-05-06)
    wireNotifToggle("toggle-desktop-global", "Desktop notifications");
    wireNotifToggle("toggle-email-global",   "Email notifications");
    // Granular desktop sub-toggles
    wireNotifToggle("toggle-milestones",  "Milestone toasts");
    wireNotifToggle("toggle-first-deal",  "First-deal toast");
    wireNotifToggle("toggle-kill-switch", "Update banner");

    // ----- manage / cancel subscription ------------------------------
    //
    // Calls /api/billing/portal -> Stripe-hosted page where the user
    // can change card, switch plan, view invoices, or CANCEL. Opens
    // in a new tab so they can come back to the app after.

    var manageBtn = document.getElementById("manage-subscription");
    if (manageBtn) {
        manageBtn.addEventListener("click", async function () {
            manageBtn.disabled = true;
            manageBtn.textContent = "Opening Stripe...";
            try {
                var res = await b.apiPost("/api/billing/portal", {});
                if (!res.url) throw new Error("no portal URL returned");
                window.open(res.url, "_blank");
                manageBtn.textContent = "Manage / cancel";
                manageBtn.disabled = false;
            } catch (e) {
                manageBtn.textContent = "Try again";
                manageBtn.disabled = false;
                b.toast(b.describeError(e), "error");
            }
        });
    }

    // ----- delete account --------------------------------------------

    document.getElementById("delete-account").addEventListener("click", async function () {
        if (!await b.confirm(
            "Delete your account? This removes all watches, subscribers, and listings. Cannot be undone.",
            { ok: "Delete account", danger: true })) return;
        if (!await b.confirm(
            "Are you absolutely sure? This is your last warning.",
            { ok: "Yes, delete", danger: true })) return;
        try {
            await b.apiPost("/api/account/delete", {});
            b.toast("Account deleted. Signing out…", "success");
            setTimeout(function () { window.location.href = "/auth"; }, 800);
        } catch (e) {
            b.toast(b.describeError(e), "error");
        }
    });

    // ----- changelog modal -------------------------------------------
    //
    // "What's New" pattern from Wispr Flow. Loads CHANGELOG.md via
    // /api/changelog, drops it into a <pre> tag (markdown stays as
    // plain text — heading hashes, bullet dashes, etc. read fine).
    // Could pull in a real markdown lib later for prettier rendering.

    var clModal = document.getElementById("changelog-modal");
    var clBody = document.getElementById("cl-modal-body");
    // Changelog modal uses the same `.is-open` class convention as the
    // referral modal (single source of truth — see app_shell.html).
    function closeChangelog() {
        clModal.classList.remove("is-open");
        clModal.removeAttribute("hidden");  // legacy attr from template
    }
    function isChangelogOpen() {
        return clModal.classList.contains("is-open");
    }
    clModal.querySelectorAll("[data-cl-close]").forEach(function (el) {
        el.addEventListener("click", closeChangelog);
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && isChangelogOpen()) closeChangelog();
    });
    document.getElementById("open-changelog").addEventListener("click", async function () {
        clModal.removeAttribute("hidden");
        clModal.classList.add("is-open");
        clBody.textContent = "loading...";
        try {
            var res = await b.apiGet("/api/changelog");
            clBody.textContent = res.markdown || "(no changelog yet)";
        } catch (e) {
            clBody.textContent = "Could not load changelog: " + b.describeError(e);
        }
    });

    // ----- about block -----------------------------------------------

    async function loadAbout() {
        try {
            var s = await b.apiGet("/api/dashboard/summary");
            document.getElementById("about-kill").textContent = "active";
        } catch (e) {
            // Even when not paid, summary returns 200; if we got 503
            // it's the kill switch.
            if (e && e.status === 503) {
                document.getElementById("about-kill").textContent = "kill-switched (please update)";
            }
        }
        // Version isn't exposed via an HTTP endpoint; leave a TODO.
        document.getElementById("about-version").textContent = "see About in app menu";
    }

    document.addEventListener("DOMContentLoaded", function () {
        loadSettings();
        loadAbout();
    });
})();
