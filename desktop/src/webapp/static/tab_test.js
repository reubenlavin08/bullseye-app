/* Test tab — two-stage flow ported from personal deal_finder/.
 *
 * Stage 1: search FB Marketplace via /api/search → render listing cards
 * Stage 2: per-listing "Appraise" button → /appraise → score appears
 *
 * eBay comp data is INTERNAL — we never show it to the user as if it
 * were the search result. The previous version did, which the user
 * correctly flagged as wrong UX. */
(function () {
    "use strict";
    var b = window.bullseye;

    var form = document.getElementById("search-form");
    var statusEl = document.getElementById("test-status");
    var resultsEl = document.getElementById("test-results");
    var submitBtn = document.getElementById("t-submit");
    if (!form) return;

    /* ---------- Quick estimate (standalone manual appraisal) ----------
     *
     * Lets the user appraise a specific item by typing the model + price
     * directly — useful when no Marketplace listing has the exact specs
     * in the title. Hits /appraise with no listing_url, so it goes
     * straight through LLM normalize → eBay comps → score. The result
     * renders inline using the same scoreCard() the listing flow uses.
     */
    var qeForm = document.getElementById("quick-est-form");
    var qeResult = document.getElementById("quick-est-result");

    function fmtMoney(v) {
        if (v == null || !isFinite(v)) return "—";
        return "$" + Math.round(Number(v)).toLocaleString();
    }

    function renderLookupCard(res) {
        // Lookup mode: NO asking price involved, NO score. Just
        // shows the user the comp distribution so they can decide
        // what a fair offer would be.
        if (res.unscoreable) {
            return '<div class="card" style="padding:14px;">' +
                '<div style="font-weight:700;font-size:14px;">' +
                'Couldn’t find similar items to compare</div>' +
                '<div class="muted" style="font-size:12px;margin-top:6px;">' +
                'Try a more specific name (year + brand + model)' +
                ' or check the spelling.</div>' +
                '</div>';
        }
        var typical = fmtMoney(res.typical_price);
        var fair = fmtMoney(res.fair_value);
        var lo = fmtMoney(res.range_low);
        var hi = fmtMoney(res.range_high);
        return '<div class="card" style="padding:18px;">' +
            (res.canonical_kind
                ? '<div class="muted" style="font-size:11px;margin-bottom:6px;">' +
                  'Searched as: <strong style="color:var(--fg);">' +
                  b.escapeHTML(res.canonical_kind) + '</strong></div>'
                : '') +
            '<div style="display:flex;gap:24px;flex-wrap:wrap;align-items:baseline;">' +
                '<div>' +
                  '<div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Typical price</div>' +
                  '<div style="font-family:var(--serif,Georgia);font-weight:700;font-size:32px;line-height:1.05;margin-top:2px;">' +
                    typical + '</div>' +
                '</div>' +
                '<div>' +
                  '<div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Fair offer</div>' +
                  '<div style="font-weight:600;font-size:18px;color:var(--good,#5d7a4f);margin-top:2px;">' +
                    fair + '</div>' +
                  '<div class="muted" style="font-size:10px;">85% of typical</div>' +
                '</div>' +
                '<div>' +
                  '<div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Common range</div>' +
                  '<div style="font-weight:500;font-size:14px;margin-top:2px;">' +
                    lo + '<span class="muted"> – </span>' + hi + '</div>' +
                  '<div class="muted" style="font-size:10px;">middle 50% of listings</div>' +
                '</div>' +
                '<div>' +
                  '<div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Based on</div>' +
                  '<div style="font-weight:500;font-size:14px;margin-top:2px;">' +
                    res.sample_size + ' similar items</div>' +
                  '<div class="muted" style="font-size:10px;">' +
                    b.escapeHTML(res.source || "ebay") + '</div>' +
                '</div>' +
            '</div>' +
            '</div>';
    }

    if (qeForm && qeResult) {
        qeForm.addEventListener("submit", async function (ev) {
            ev.preventDefault();
            var term = (document.getElementById("qe-term").value || "").trim();
            if (!term) {
                qeResult.hidden = false;
                qeResult.innerHTML =
                    '<div class="muted" style="color:var(--bad);">' +
                    'Enter what you’re looking for.</div>';
                return;
            }
            qeResult.hidden = false;
            qeResult.innerHTML =
                '<div class="muted">Looking up similar items…</div>';
            try {
                var res = await b.apiPost("/api/lookup", {
                    title: term,
                    region: "EBAY-ENCA",
                });
                qeResult.innerHTML = renderLookupCard(res);
            } catch (e) {
                qeResult.innerHTML =
                    '<span style="color:var(--bad);font-size:12px;">' +
                    b.escapeHTML(b.describeError(e)) + '</span>';
            }
        });
    }

    /* ---------- listing card ----------------------------------------- */

    function listingCard(it) {
        var photo = it.photo_url
            ? '<img class="tl-photo" src="' + b.escapeHTML(it.photo_url) + '" alt="" loading="lazy">'
            : '<div class="tl-photo tl-photo-empty">no photo</div>';
        var price = it.price_formatted
            || (it.price_amount != null ? b.fmtMoney(it.price_amount) : "—");
        var loc = it.seller_location
            ? '<span class="tl-loc muted">' + b.escapeHTML(it.seller_location) + '</span>'
            : '';
        var pending = it.is_pending
            ? '<span class="tl-tag">pending</span>' : '';
        var prev = it.previous_price
            ? '<span class="tl-tag">was ' + b.escapeHTML(it.previous_price) + '</span>'
            : '';
        var titleLink = it.listing_url
            ? '<a href="' + b.escapeHTML(b.safeUrl(it.listing_url)) + '" target="_blank" rel="noopener">' + b.escapeHTML(it.title || "(untitled)") + '</a>'
            : b.escapeHTML(it.title || "(untitled)");
        return '<article class="tl-card" '
            + 'data-listing-id="' + b.escapeHTML(it.id) + '" '
            + 'data-listing-url="' + b.escapeHTML(it.listing_url || "") + '" '
            + 'data-body="' + b.escapeHTML((it.description || "").slice(0, 800)) + '" '
            + 'data-title="' + b.escapeHTML(it.title || "") + '" '
            + 'data-price="' + (it.price_amount != null ? it.price_amount : "") + '">'
            +   photo
            +   '<div class="tl-body">'
            +     '<div class="tl-price">' + b.escapeHTML(String(price)) + '</div>'
            +     '<h3 class="tl-title">' + titleLink + '</h3>'
            +     '<div class="tl-meta">' + loc + ' ' + pending + ' ' + prev + '</div>'
            +     '<div class="tl-actions" style="display:flex;gap:6px;flex-wrap:wrap;">'
            +       '<button type="button" class="btn btn-primary tl-appraise-btn">What’s this worth?</button>'
            +       '<button type="button" class="btn btn-ghost btn-tiny tl-fetch-desc-btn" title="Pull the full description from the listing page so we have more info to work with.">See description</button>'
            +     '</div>'
            +     '<div class="tl-fetch-status muted" hidden style="font-size:11px;margin-top:4px;"></div>'
            +     '<div class="tl-fetched-body" hidden style="margin-top:8px;padding:10px 12px;background:var(--bg-sunk,#f3efe7);border-left:3px solid var(--accent,#c2410c);border-radius:6px;font-size:12px;line-height:1.5;white-space:pre-wrap;max-height:240px;overflow-y:auto;"></div>'
            +     '<div class="tl-appraisal" hidden></div>'
            +   '</div>'
            + '</article>';
    }

    /* ---------- search submit ---------------------------------------- */

    form.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        var keyword = document.getElementById("t-keyword").value.trim();
        if (!keyword) {
            statusEl.textContent = "Enter a keyword first.";
            return;
        }
        var payload = {
            keyword: keyword,
            radius_km: Number(document.getElementById("t-radius").value || 40),
        };
        var pmin = document.getElementById("t-pmin").value.trim();
        var pmax = document.getElementById("t-pmax").value.trim();
        if (pmin) payload.price_min = Number(pmin);
        if (pmax) payload.price_max = Number(pmax);

        submitBtn.disabled = true;
        statusEl.textContent = "Searching Marketplace...";
        resultsEl.innerHTML = "";

        try {
            var res = await b.apiPost("/api/search", payload);
            var elapsed = res.elapsed_ms != null ? (res.elapsed_ms + "ms") : "";
            if (!res.listings || res.listings.length === 0) {
                statusEl.innerHTML = res.error_message
                    ? '<span style="color:var(--bad)">' + b.escapeHTML(res.error_message) + '</span>'
                    : 'No Marketplace listings for "' + b.escapeHTML(keyword) + '". Try a different keyword or widen the radius.';
                return;
            }
            statusEl.innerHTML = '<strong>' + res.listings.length + '</strong> listings · '
                + b.escapeHTML(elapsed)
                + (res.has_more ? ' · more pages available (not loaded)' : '');
            resultsEl.innerHTML = res.listings.map(listingCard).join("");
        } catch (e) {
            statusEl.innerHTML = '<span style="color:var(--bad)">'
                + b.escapeHTML(b.describeError(e)) + '</span>';
        } finally {
            submitBtn.disabled = false;
        }
    });

    /* ---------- per-listing appraise --------------------------------- */

    /* D3: delegate to the shared interactive score card. The old inline
     * markup (tl-score / tl-score-unscoreable) is kept in shell.css for
     * any legacy surface still rendering it, but new appraisals use the
     * richer component with confidence band + click-to-expand
     * breakdown. */
    function renderAppraisal(res) {
        if (typeof b.scoreCard === "function") {
            return b.scoreCard(res);
        }
        // Fallback if score_component.js failed to load — keep the
        // listing card from breaking outright.
        return res.unscoreable
            ? '<div class="muted">Not enough data to score.</div>'
            : '<div>Score: ' + (res.deal_score != null ? res.deal_score : "—") + '</div>';
    }

    // "Fetch description" — pulls the full listing body via the
    // server-side FacebookDetailClient (PDP-first, HTML fallback).
    // Updates the card's data-body in place; if the card was already
    // appraised, also automatically re-runs the appraisal so the
    // LLM normalize call gets the richer body and produces a better
    // canonical_kind. Without this button, listings that have empty
    // body text in the search-page response (very common — most
    // Marketplace search cards have no description until you open
    // them) get only the bare title for normalize, which is hard to
    // match against eBay comps. This is the explicit way for the
    // user to enrich a listing before scoring.
    resultsEl.addEventListener("click", async function (ev) {
        var fbtn = ev.target.closest(".tl-fetch-desc-btn");
        if (!fbtn) return;
        var fcard = fbtn.closest(".tl-card");
        if (!fcard) return;
        var listingId = fcard.getAttribute("data-listing-id") || "";
        if (!listingId) return;
        var status = fcard.querySelector(".tl-fetch-status");
        fbtn.disabled = true;
        fbtn.textContent = "Fetching...";
        status.hidden = false;
        status.textContent = "Pulling listing description...";
        try {
            var det = await b.apiPost(
                "/api/listing/" + encodeURIComponent(listingId) + "/detail",
                {},
            );
            var desc = (det && det.description) || "";
            if (!desc) {
                status.style.color = "var(--bad)";
                status.textContent = "No description available on this listing.";
                fbtn.textContent = "No description";
                fbtn.disabled = true;
                return;
            }
            // Stash on the card so the next /appraise call sends it.
            fcard.setAttribute("data-body", desc.slice(0, 800));
            // Show the actual fetched text so the user can verify the
            // pull worked AND see what the LLM normalize call will be
            // working with on the re-appraise. Cap at 1500 chars
            // displayed (full text is sent to /appraise; we just don't
            // want to flood the test screen with War-and-Peace-length
            // listings).
            var bodyEl = fcard.querySelector(".tl-fetched-body");
            if (bodyEl) {
                var displayed = desc.length > 1500
                    ? desc.slice(0, 1500) + "\n\n…(" + (desc.length - 1500) + " more chars)"
                    : desc;
                bodyEl.textContent = displayed;
                bodyEl.hidden = false;
            }
            status.style.color = "";
            status.textContent = "Description pulled (" + desc.length + " chars). " +
                                 (det.source ? "[" + det.source + "] " : "") +
                                 "Re-appraising...";
            fbtn.textContent = "Description fetched";
            // Auto re-appraise: programmatically click the appraise
            // button. Avoids a duplicate code path AND ensures the
            // user sees the new score immediately.
            var apprBtn = fcard.querySelector(".tl-appraise-btn");
            if (apprBtn && !apprBtn.disabled) {
                // If the card was previously appraised, the button
                // text says "Re-appraise (fresh comps)" — that path
                // already passes force_refresh=true which is exactly
                // what we want here (cache may have been keyed on the
                // old empty-body hash).
                apprBtn.click();
            }
        } catch (e) {
            status.style.color = "var(--bad)";
            status.textContent = b.describeError(e);
            fbtn.textContent = "Try again";
            fbtn.disabled = false;
        }
    });

    resultsEl.addEventListener("click", async function (ev) {
        var btn = ev.target.closest(".tl-appraise-btn");
        if (!btn) return;
        var card = btn.closest(".tl-card");
        if (!card) return;
        var title = card.getAttribute("data-title") || "";
        var price = parseFloat(card.getAttribute("data-price") || "");
        // listing_url + body are passed through to /appraise so the
        // server can call appraise-normalize for canonical_kind +
        // worth_deep + red_flags. Both are optional — if the card
        // doesn't have them, /appraise just skips normalization and
        // does the legacy raw-title comp lookup.
        var listingUrl = card.getAttribute("data-listing-url") || "";
        var bodyText = card.getAttribute("data-body") || "";
        if (!title || !isFinite(price)) {
            btn.disabled = true;
            btn.textContent = "Can't score — missing title or price";
            return;
        }
        var pane = card.querySelector(".tl-appraisal");
        // "Refresh price data" button passes force_refresh=true to
        // bypass the cached comparable-price data. Useful when the
        // cached data is obviously wrong (e.g. mostly parts) and we
        // just shipped a tighter filter that would catch them.
        var isReappraise = /Refresh price data/i.test(btn.textContent);
        btn.disabled = true;
        btn.textContent = isReappraise ? "Refreshing data..." : "Working...";
        pane.hidden = false;
        pane.innerHTML = '<div class="muted">looking up similar items...</div>';
        try {
            var res = await b.apiPost("/appraise", {
                title: title,
                asking_price: price,
                region: "EBAY_US",
                force_refresh: isReappraise,
                listing_url: listingUrl,
                body: bodyText,
            });
            pane.innerHTML = renderAppraisal(res);
            btn.textContent = "Refresh price data";
            btn.disabled = false;
        } catch (e) {
            pane.innerHTML = '<span style="color:var(--bad);font-size:12px;">'
                + b.escapeHTML(b.describeError(e)) + '</span>';
            btn.textContent = "Try again";
            btn.disabled = false;
        }
    });
})();
