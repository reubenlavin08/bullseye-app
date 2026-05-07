/* Watches tab — CRUD against /api/watches plus bulk-update. */
(function () {
    "use strict";
    var b = window.bullseye;

    var listEl = document.getElementById("watch-list");

    // Module-level flag — true once we know the user has a real
    // home location set. Used by the "+ New search" gate to disable
    // the button + show a clear "set location first" hint instead of
    // letting the user fill out the form and hit a 400 on submit.
    var _locationIsSet = false;

    // Location banner: read /api/settings, show the user's city+radius,
    // or prompt them to set one if missing. The "Set location" button
    // points at /settings#location which scrolls Settings to the right
    // section.
    async function refreshLocationBanner() {
        var statusEl = document.getElementById("home-location-status");
        var ctaEl = document.getElementById("home-location-cta");
        if (!statusEl || !ctaEl) return;
        try {
            var s = await b.apiGet("/api/settings");
            var hasLat = s && s.home_latitude != null;
            var hasLng = s && s.home_longitude != null;
            var city = (s && s.home_city) || "";
            var radius = (s && s.home_radius_km) || null;
            _locationIsSet = !!(hasLat && hasLng);
            if (_locationIsSet && city && radius) {
                statusEl.innerHTML =
                    "Searching from <strong>" + b.escapeHTML(city) +
                    "</strong> · " + radius + " km radius";
                ctaEl.textContent = "Change";
                ctaEl.classList.remove("btn-primary");
                ctaEl.classList.add("btn-ghost");
            } else if (_locationIsSet) {
                // Lat/lng set but no friendly label — show coords.
                statusEl.innerHTML =
                    "Searching from <strong>" + Number(s.home_latitude).toFixed(3) +
                    ", " + Number(s.home_longitude).toFixed(3) + "</strong>";
                ctaEl.textContent = "Change";
                ctaEl.classList.remove("btn-primary");
                ctaEl.classList.add("btn-ghost");
            } else {
                statusEl.innerHTML =
                    '<span style="color:var(--accent);font-weight:500;">' +
                    'Set your home location to start searching.</span>';
                ctaEl.textContent = "Set location";
                ctaEl.classList.add("btn-primary");
                ctaEl.classList.remove("btn-ghost");
            }
        } catch (e) {
            statusEl.textContent =
                "Set your home city + radius in Settings.";
            _locationIsSet = false;
        }
        // Apply the gate to the "+ New search" button now that we
        // know the location-set state.
        applyLocationGate();
    }

    /* Disable the "+ New search" button and the form's submit when
       no home location is set. The server-side gate in /api/watches
       returns 400 with error="location_required" — but blocking the
       UI BEFORE the user fills out the form is much better UX. */
    function applyLocationGate() {
        var newBtn = document.getElementById("new-watch-btn");
        var pollBtn = document.getElementById("poll-now-btn");
        var formWrap = document.getElementById("new-watch-form-wrap");
        if (newBtn) {
            newBtn.disabled = !_locationIsSet;
            newBtn.title = _locationIsSet ? "" :
                "Set your home location first";
        }
        if (pollBtn) {
            pollBtn.disabled = !_locationIsSet;
        }
        // If the form is open and they un-set the location, hide it.
        if (!_locationIsSet && formWrap && !formWrap.hidden) {
            formWrap.hidden = true;
        }
    }

    function watchRow(w) {
        var paused = !w.active;
        var meta = [];
        if (w.radius_km != null) meta.push(w.radius_km + " km");
        if (w.price_min != null) meta.push("min $" + w.price_min);
        if (w.price_max != null) meta.push("max $" + w.price_max);
        if (w.score_threshold != null) meta.push("≥ " + w.score_threshold);
        // Per-watch `email` is deprecated — alert routing is now
        // account-wide (Settings → Notifications). Old rows may
        // still carry an email value; we silently ignore it.
        var statsBits = [];
        statsBits.push((w.hit_count || 0) + " matches");
        statsBits.push((w.total_seen || 0) + " listings checked");
        if (!w.last_scrape) {
            // Never searched yet — explicitly tell the user the
            // first check hasn't run yet so a "0 listings checked"
            // doesn't read as "broken".
            statsBits.push('<span style="color:var(--accent);">first check coming up…</span>');
        }
        var stats = statsBits.join(" / ");

        return ''
            + '<div class="watch-row" data-paused="' + (paused ? 'true' : 'false') + '" data-id="' + w.id + '">'
            +   '<div>'
            +     '<div class="watch-keyword">' + b.escapeHTML(w.keyword) + '</div>'
            +     '<div class="watch-meta">' + meta.join(" · ") + '</div>'
            +     '<div class="watch-meta">' + stats + (w.last_scrape ? " · last scrape " + b.fmtRelative(w.last_scrape) : "") + '</div>'
            +   '</div>'
            +   '<div class="watch-actions">'
            +     '<button class="btn-tiny" data-action="toggle">' + (paused ? "Resume" : "Pause") + '</button>'
            +     '<button class="btn-tiny" data-action="edit">Edit</button>'
            +     '<button class="btn-tiny" data-action="delete">Delete</button>'
            +   '</div>'
            +   '<div class="watch-edit">'
            +     '<div class="input-row">'
            +       '<div class="field"><label class="field-label">Score threshold</label><input type="number" data-edit="score_threshold" min="0" max="100" value="' + (w.score_threshold != null ? w.score_threshold : '') + '"></div>'
            +       '<div class="field"><label class="field-label">Radius (km)</label><input type="number" data-edit="radius_km" min="1" max="500" value="' + (w.radius_km != null ? w.radius_km : '') + '"></div>'
            +       '<div class="field"><label class="field-label">Min $</label><input type="number" data-edit="price_min" min="0" value="' + (w.price_min != null ? w.price_min : '') + '"></div>'
            +       '<div class="field"><label class="field-label">Max $</label><input type="number" data-edit="price_max" min="0" value="' + (w.price_max != null ? w.price_max : '') + '"></div>'
            +     '</div>'
            +     '<div style="display:flex;gap:8px;margin-top:6px;">'
            +       '<button class="btn btn-primary btn-tiny" data-action="save">Save</button>'
            +       '<button class="btn btn-ghost btn-tiny" data-action="cancel-edit">Cancel</button>'
            +     '</div>'
            +   '</div>'
            + '</div>';
    }

    async function loadWatches() {
        listEl.innerHTML = '<div class="muted">loading…</div>';
        try {
            var res = await b.apiGet("/api/watches");
            var watches = res.watches || [];
            if (!watches.length) {
                listEl.innerHTML = '<div class="muted">No watches yet. Click "+ New watch" to add one.</div>';
                return;
            }
            listEl.innerHTML = watches.map(watchRow).join("");
        } catch (e) {
            listEl.innerHTML = '<div class="muted">' + b.escapeHTML(b.describeError(e)) + '</div>';
        }
    }

    listEl.addEventListener("click", async function (ev) {
        var btn = ev.target.closest("button[data-action]");
        if (!btn) return;
        var row = btn.closest(".watch-row");
        if (!row) return;
        var id = row.getAttribute("data-id");
        var action = btn.getAttribute("data-action");

        if (action === "toggle") {
            var nowPaused = row.getAttribute("data-paused") === "true";
            try {
                await b.apiPatch("/api/watches/" + id, { active: nowPaused });
                b.toast(nowPaused ? "Search resumed." : "Search paused.", "success");
                loadWatches();
            } catch (e) {
                b.toast(b.describeError(e), "error");
            }
        } else if (action === "edit") {
            row.classList.add("is-editing");
        } else if (action === "cancel-edit") {
            row.classList.remove("is-editing");
        } else if (action === "save") {
            var payload = {};
            row.querySelectorAll("[data-edit]").forEach(function (input) {
                var k = input.getAttribute("data-edit");
                var v = input.value.trim();
                if (v === "") {
                    payload[k] = null;
                } else {
                    payload[k] = Number(v);
                }
            });
            try {
                await b.apiPatch("/api/watches/" + id, payload);
                b.toast("Search updated.", "success");
                loadWatches();
            } catch (e) {
                b.toast(b.describeError(e), "error");
            }
        } else if (action === "delete") {
            if (!await b.confirm("Delete this watch? This is permanent.",
                { ok: "Delete", danger: true })) return;
            try {
                await b.apiDelete("/api/watches/" + id);
                b.toast("Search deleted.", "success");
                loadWatches();
            } catch (e) {
                b.toast(b.describeError(e), "error");
            }
        }
    });

    // ----- new-watch form --------------------------------------------

    var newBtn = document.getElementById("new-watch-btn");
    var newWrap = document.getElementById("new-watch-form-wrap");
    var newForm = document.getElementById("new-watch-form");
    var cancelBtn = document.getElementById("new-watch-cancel");

    newBtn.addEventListener("click", function () {
        newWrap.hidden = !newWrap.hidden;
        if (!newWrap.hidden) document.getElementById("nw-keyword").focus();
    });
    cancelBtn.addEventListener("click", function () {
        newWrap.hidden = true;
        newForm.reset();
    });

    // Bulk-add toggle: switches between single keyword input and a
    // textarea where each line becomes its own watch with the same
    // shared filters. Re-added 2026-05-06.
    // Single-textarea toggle: same DOM element, just grows + changes
    // placeholder + adds a CSS class when flipped to multi-search.
    // Much more robust than the previous two-field swap.
    var bulkToggle = document.getElementById("nw-bulk-toggle");
    var kwField = document.getElementById("nw-keyword");
    var kwLabel = document.getElementById("nw-keyword-label");
    var bulkHint = document.getElementById("nw-bulk-hint");
    var toggleLabel = document.getElementById("nw-toggle-label");
    if (bulkToggle && kwField) {
        function applyBulkToggle() {
            var bulk = bulkToggle.checked;
            if (bulk) {
                kwField.classList.add("nw-keyword-bulk");
                kwField.rows = 6;
                kwField.placeholder =
                    "MacBook Pro 14 M2\nAeron Size B\nYamaha receiver\nSony A7";
                if (kwLabel) kwLabel.textContent =
                    "What are you searching for? (one per line)";
                if (bulkHint) bulkHint.hidden = false;
                if (toggleLabel) toggleLabel.textContent = "Multiple searches";
            } else {
                kwField.classList.remove("nw-keyword-bulk");
                kwField.rows = 1;
                kwField.placeholder =
                    "2018 Honda Civic, MacBook Pro 14 M2…";
                if (kwLabel) kwLabel.textContent =
                    "What are you searching for?";
                if (bulkHint) bulkHint.hidden = true;
                if (toggleLabel) toggleLabel.textContent = "Multiple searches";
            }
        }
        bulkToggle.addEventListener("change", applyBulkToggle);
        bulkToggle.addEventListener("click", applyBulkToggle);
        applyBulkToggle();
    }

    newForm.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        var fd = new FormData(newForm);
        // Common shared payload (everything except the keyword)
        var common = {};
        fd.forEach(function (v, k) {
            v = String(v).trim();
            if (v === "" || k === "keyword" || k === "keywords_bulk") return;
            common[k] = v;
        });

        var bulk = bulkToggle && bulkToggle.checked;
        var keywords;
        if (bulk) {
            // The single keyword field IS the bulk textarea when
            // toggled — read newlines from it directly.
            var raw = (kwField && kwField.value) || "";
            keywords = raw.split(/\r?\n/)
                .map(function (s) { return s.trim(); })
                .filter(function (s) { return s.length > 0; });
            if (!keywords.length) {
                b.toast("Add at least one keyword (one per line).", "error");
                return;
            }
        } else {
            var single = (fd.get("keyword") || "").trim();
            if (!single) { b.toast("Keyword required.", "error"); return; }
            keywords = [single];
        }

        // Submit serially so one failure doesn't kill the rest. Track
        // counts for a single rollup toast at the end.
        var ok = 0, fail = 0, lastErr = null;
        for (var i = 0; i < keywords.length; i++) {
            var p = Object.assign({}, common, { keyword: keywords[i] });
            try {
                await b.apiPost("/api/watches", p);
                ok++;
            } catch (e) {
                fail++;
                lastErr = e;
            }
        }
        if (ok && !fail) {
            b.toast(ok === 1 ? "Search saved." : ok + " searches saved.", "success");
        } else if (ok && fail) {
            b.toast(ok + " saved, " + fail + " failed (" + b.describeError(lastErr) + ").", "error");
        } else {
            b.toast("Could not save watches: " + b.describeError(lastErr), "error");
        }
        newForm.reset();
        if (bulkToggle) {
            bulkToggle.checked = false;
            singleWrap.hidden = false;
            bulkWrap.hidden = true;
            document.getElementById("nw-keyword").required = true;
        }
        newWrap.hidden = true;
        loadWatches();
    });

    document.getElementById("refresh-watches").addEventListener("click", loadWatches);

    // "Search now" — manual trigger for visibility / testing.
    // Server kicks the polls in the background and returns immediately;
    // we then auto-refresh the watch list every 5s for 60s so the user
    // sees last_scrape + hit_count update in real time.
    var pollNowBtn = document.getElementById("poll-now-btn");
    var pollNowStatus = document.getElementById("poll-now-status");
    if (pollNowBtn) {
        pollNowBtn.addEventListener("click", async function () {
            pollNowBtn.disabled = true;
            pollNowBtn.textContent = "Starting...";
            pollNowStatus.hidden = false;
            pollNowStatus.style.color = "";
            pollNowStatus.textContent = "Kicking polls...";
            try {
                var res = await b.apiPost("/api/watches/poll-now", {});
                if (res.started === 0) {
                    pollNowStatus.textContent = res.message || "No active searches.";
                    pollNowBtn.disabled = false;
                    pollNowBtn.textContent = "Search now";
                    return;
                }
                pollNowStatus.textContent =
                    "Searching " + res.started + " saved search(es) in the background. " +
                    "List will refresh as results come in.";
                pollNowBtn.textContent = "Searching…";
                // Auto-refresh the watch list every 5s for the next 60s
                // so the user sees last_scrape + counts update without
                // hitting the Refresh button.
                var ticks = 0;
                var iv = setInterval(function () {
                    ticks += 1;
                    loadWatches();
                    if (ticks >= 12) {
                        clearInterval(iv);
                        pollNowBtn.disabled = false;
                        pollNowBtn.textContent = "Search now";
                        pollNowStatus.textContent =
                            "Done. Check the search results above.";
                    }
                }, 5000);
            } catch (e) {
                pollNowStatus.style.color = "var(--bad)";
                pollNowStatus.textContent = b.describeError(e);
                pollNowBtn.disabled = false;
                pollNowBtn.textContent = "Search now";
            }
        });
    }

    // ----- bulk edit --------------------------------------------------

    document.getElementById("bulk-apply-btn").addEventListener("click", async function () {
        var payload = {};
        function pull(checkboxId, inputId, name, asInt) {
            var cb = document.getElementById(checkboxId);
            if (!cb || !cb.checked) return;
            var inp = document.getElementById(inputId);
            var v = inp.value.trim();
            if (asInt && v === "") {
                payload[name] = null;
            } else if (v === "") {
                payload[name] = null;
            } else {
                payload[name] = asInt ? Number(v) : v;
            }
        }
        pull("be-thresh-on", "be-thresh", "score_threshold", true);
        pull("be-radius-on", "be-radius", "radius_km", true);
        pull("be-pmin-on", "be-pmin", "price_min", true);
        pull("be-pmax-on", "be-pmax", "price_max", true);
        if (document.getElementById("be-active-on").checked) {
            payload.active = document.getElementById("be-active").value === "true";
        }
        if (Object.keys(payload).length === 0) {
            b.toast("Tick at least one field to apply.", "error");
            return;
        }
        try {
            var res = await b.apiPost("/api/watches/bulk-update", payload);
            b.toast(
                "Updated " + (res.watches_updated || 0) + " watch(es).",
                "success"
            );
            loadWatches();
        } catch (e) {
            b.toast(b.describeError(e), "error");
        }
    });

    document.addEventListener("DOMContentLoaded", function () {
        loadWatches();
        refreshLocationBanner();
    });
})();
