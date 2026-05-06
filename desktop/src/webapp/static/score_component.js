/* Interactive score component — Phase D3.
 *
 * Reusable across Test, Activity, and any future surface that shows a
 * deal score. Exposes a single function:
 *
 *     bullseye.scoreCard(res, opts) -> HTML string
 *
 * Where `res` matches the /appraise response shape:
 *   { ok, unscoreable, deal_score, fair_value, asking_price,
 *     comp_median, comp_sample_size, raw_comps: [{price}, ...],
 *     comp_source, search_term }
 *
 * The component:
 *   1. Big tier-coded score number with verdict tag
 *      (Great deal / Good / Fair / Overpriced)
 *   2. Confidence band: horizontal range showing comp price distribution
 *      (min..p25..median..p75..max) with a marker at the asking price
 *      so you can SEE where this listing sits at a glance
 *   3. "Why this score" expander → fair value, median, sample size,
 *      comp source, raw-comp dot strip
 *   4. Click-to-expand (no JS framework, native <details>)
 *
 * Accessibility: <details>/<summary> handles keyboard + screen readers
 * for free; tier color is paired with a text label so colorblind users
 * still get the verdict.
 *
 * NOTE: this file is loaded by app_shell.html BEFORE per-tab scripts,
 * so b.scoreCard is available everywhere. */
(function () {
    "use strict";
    var b = window.bullseye = window.bullseye || {};

    // --- helpers (some duplicate b.* in case shell.js loads later) -----

    function esc(s) {
        return String(s == null ? "" : s)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;")
            .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function money(v) {
        if (v == null || !isFinite(v)) return "—";
        return "$" + Number(v).toLocaleString(undefined, {
            minimumFractionDigits: 0, maximumFractionDigits: 0,
        });
    }

    /** Pick a percentile from a sorted price array. q in [0,1]. */
    function quantile(sorted, q) {
        if (!sorted || !sorted.length) return null;
        if (sorted.length === 1) return sorted[0];
        var pos = (sorted.length - 1) * q;
        var base = Math.floor(pos);
        var rest = pos - base;
        if (sorted[base + 1] !== undefined) {
            return sorted[base] + rest * (sorted[base + 1] - sorted[base]);
        }
        return sorted[base];
    }

    // --- verdict mapping -----------------------------------------------
    //
    // Tier copy is intentionally short — readable in a tight card.
    // Threshold rationale:
    //   ≥80  great    asking price is in the cheapest 20% of comps
    //   60-79 good    cheaper than median
    //   40-59 fair    near median
    //   <40  over     more expensive than median
    function tierFor(score) {
        if (score == null || !isFinite(score)) {
            return { cls: "sc-unknown", label: "—", verdict: "no score" };
        }
        if (score >= 80) return { cls: "sc-great",  label: "Great deal", verdict: "well below comp median" };
        if (score >= 60) return { cls: "sc-good",   label: "Good deal",  verdict: "cheaper than the median comp" };
        if (score >= 40) return { cls: "sc-fair",   label: "Fair",       verdict: "near the median comp" };
        return                { cls: "sc-over",   label: "Overpriced", verdict: "above the median comp" };
    }

    // --- confidence band -----------------------------------------------
    //
    // Renders an SVG showing the comp price distribution with the
    // asking price marked. The "box" is p25..p75 (the middle 50% of
    // comps); the median is a dark tick; whiskers extend to min/max.
    // The asking price marker is positioned along the same axis so the
    // user can SEE the relationship without reading numbers.
    function confidenceBand(askingPrice, comps) {
        if (!comps || comps.length < 3) return "";
        var prices = comps
            .map(function (c) { return Number(c.price); })
            .filter(function (p) { return isFinite(p) && p > 0; })
            .sort(function (a, c) { return a - c; });
        if (prices.length < 3) return "";

        var min = prices[0];
        var max = prices[prices.length - 1];
        if (max <= min) return "";

        var p25 = quantile(prices, 0.25);
        var p50 = quantile(prices, 0.50);
        var p75 = quantile(prices, 0.75);

        // Pad the axis a bit beyond min/max so the marker doesn't sit
        // exactly on the edge if it's outside the comp range.
        var padded = (max - min) * 0.08;
        var axisMin = Math.min(min, askingPrice) - padded;
        var axisMax = Math.max(max, askingPrice) + padded;
        var span = axisMax - axisMin;

        var pos = function (v) {
            return Math.max(0, Math.min(100,
                ((v - axisMin) / span) * 100,
            ));
        };

        var x25  = pos(p25);
        var x75  = pos(p75);
        var x50  = pos(p50);
        var xAsk = pos(askingPrice);
        var askBelowMedian = askingPrice < p50;

        // Build the inline SVG. Width 100%, height ~28px (compact).
        // Box = p25..p75, median tick, whiskers, then asking-price diamond.
        return '' +
            '<div class="sc-band-wrap">' +
            '  <div class="sc-band" role="img" ' +
            '       aria-label="Asking price ' + esc(money(askingPrice)) +
            ' vs comp range ' + esc(money(min)) + ' to ' + esc(money(max)) +
            ', median ' + esc(money(p50)) + '">' +
            '    <div class="sc-band-axis"></div>' +
            '    <div class="sc-band-box" style="left:' + x25 + '%;width:' +
                  Math.max(0.5, x75 - x25) + '%"></div>' +
            '    <div class="sc-band-median" style="left:' + x50 + '%"></div>' +
            '    <div class="sc-band-marker ' +
                  (askBelowMedian ? "sc-band-marker-good" : "sc-band-marker-over") +
            '" style="left:' + xAsk + '%" ' +
            '         title="Asking price"></div>' +
            '  </div>' +
            '  <div class="sc-band-legend muted">' +
            '    <span>' + esc(money(min)) + '</span>' +
            '    <span class="sc-band-legend-mid">median ' + esc(money(p50)) + '</span>' +
            '    <span>' + esc(money(max)) + '</span>' +
            '  </div>' +
            '</div>';
    }

    // --- raw comps strip ------------------------------------------------
    //
    // Tiny dot-per-comp visualization for the expanded breakdown. Lets
    // the user see whether the distribution is tight or spread out.
    function compDots(askingPrice, comps) {
        if (!comps || comps.length < 2) return "";
        var prices = comps
            .map(function (c) { return Number(c.price); })
            .filter(function (p) { return isFinite(p) && p > 0; })
            .sort(function (a, c) { return a - c; });
        if (prices.length < 2) return "";
        var min = prices[0];
        var max = prices[prices.length - 1];
        var span = (max - min) || 1;
        var dots = prices.map(function (p) {
            var x = ((p - min) / span) * 100;
            return '<span class="sc-comp-dot" style="left:' + x + '%" ' +
                   'title="' + esc(money(p)) + '"></span>';
        }).join("");
        var askX = ((askingPrice - min) / span) * 100;
        var askMarker = '<span class="sc-comp-ask" style="left:' +
            Math.max(0, Math.min(100, askX)) + '%" ' +
            'title="Asking ' + esc(money(askingPrice)) + '"></span>';
        return '<div class="sc-comp-strip" aria-hidden="true">' +
            dots + askMarker + '</div>';
    }

    // --- main entry -----------------------------------------------------

    /** Pretty-print a red-flag token from the LLM normalize call. */
    function redFlagLabel(token) {
        return ({
            broken_screen: "broken screen",
            water_damage: "water damage",
            icloud_locked: "iCloud locked",
            for_parts: "for parts",
            as_is: "as-is",
            bundle_of_items: "bundle",
            missing_pieces: "missing pieces",
            cosmetic_damage: "cosmetic damage",
            no_charger: "no charger",
            non_functional: "not working",
        })[token] || String(token).replace(/_/g, " ");
    }

    function redFlagsHTML(flags) {
        if (!flags || !flags.length) return "";
        return '<div class="sc-redflags">' +
            flags.map(function (f) {
                return '<span class="sc-redflag">' +
                    esc(redFlagLabel(f)) + '</span>';
            }).join("") +
            '</div>';
    }

    function appraisedAsHTML(res) {
        if (!res.canonical_kind) return "";
        return '<div class="sc-canon muted">' +
            'appraised as: <strong>' + esc(res.canonical_kind) +
            '</strong></div>';
    }

    function unscoreableCard(res) {
        // The "low_quality_data" branch is the one returned when the
        // LLM normalize call says worth_deep=false. Distinct from
        // "not enough comps" because there's nothing the user can do
        // about a low-quality LISTING; trying again won't help. The
        // copy reflects that.
        var isLowQuality = res.reason === "low_quality_data";
        var headStrong = isLowQuality
            ? "Couldn't appraise"
            : "Not enough data to score";
        var headSub = isLowQuality
            ? (res.reason_detail || "low-quality listing data")
            : (res.reason || "insufficient comparable listings");

        return '<div class="sc-card sc-unscoreable' +
                (isLowQuality ? ' sc-low-quality' : '') + '">' +
            appraisedAsHTML(res) +
            '<div class="sc-uns-head">' +
            '  <strong>' + esc(headStrong) + '</strong>' +
            '  <span class="muted"> · ' + esc(headSub) + '</span>' +
            '</div>' +
            redFlagsHTML(res.red_flags) +
            (res.sample_size != null && !isLowQuality
                ? '<div class="muted sc-uns-meta">' +
                    res.sample_size + ' comp(s)' +
                    (res.median ? ' · median ' + esc(money(res.median)) : "") +
                    (res.comp_source ? ' · ' + esc(res.comp_source) : "") +
                  '</div>'
                : "") +
            '</div>';
    }

    /**
     * Build a fully self-contained interactive score card.
     *
     * @param {object} res    /appraise response payload
     * @param {object} [opts]
     * @param {boolean} [opts.compact=false]  smaller variant for inline lists
     * @param {boolean} [opts.openByDefault=false]
     */
    b.scoreCard = function (res, opts) {
        opts = opts || {};
        if (!res || res.unscoreable) return unscoreableCard(res || {});

        var score = Number(res.deal_score);
        var tier = tierFor(score);
        var asking = Number(res.asking_price);
        var fair = Number(res.fair_value);
        var median = Number(res.comp_median);
        var n = Number(res.comp_sample_size) || (res.raw_comps || []).length;
        var src = res.comp_source || "eBay sold listings";

        var savings = (isFinite(asking) && isFinite(fair) && asking < fair)
            ? fair - asking : null;

        var savingsLine = savings != null
            ? '<span class="sc-savings">save ' + esc(money(savings)) +
              ' vs fair value</span>'
            : '';

        var summaryHTML = '' +
            '<div class="sc-card ' + tier.cls +
                (opts.compact ? ' sc-compact' : '') + '">' +
            appraisedAsHTML(res) +
            redFlagsHTML(res.red_flags) +
            '  <div class="sc-head">' +
            '    <div class="sc-num-block">' +
            '      <span class="sc-num">' + (isFinite(score) ? score : "—") +
                  '</span>' +
            '      <span class="sc-num-suffix">/100</span>' +
            '    </div>' +
            '    <div class="sc-verdict">' +
            '      <div class="sc-verdict-label">' + esc(tier.label) + '</div>' +
            '      <div class="sc-verdict-sub muted">' + esc(tier.verdict) +
                  '</div>' +
            '    </div>' +
            '    ' + savingsLine +
            '  </div>' +
            confidenceBand(asking, res.raw_comps) +
            '  <details class="sc-details"' +
            (opts.openByDefault ? " open" : "") + '>' +
            '    <summary class="sc-summary">' +
            '      <span class="sc-summary-label">Why this score</span>' +
            '      <span class="sc-chev" aria-hidden="true">&#9656;</span>' +
            '    </summary>' +
            '    <dl class="sc-grid">' +
            '      <dt>Asking price</dt>' +
            '      <dd>' + esc(money(asking)) + '</dd>' +
            '      <dt>Fair value</dt>' +
            '      <dd>' + esc(money(fair)) + ' <span class="muted">(85% of median)</span></dd>' +
            '      <dt>Comp median</dt>' +
            '      <dd>' + esc(money(median)) + '</dd>' +
            '      <dt>Sample size</dt>' +
            '      <dd>' + n + ' comp(s)</dd>' +
            '      <dt>Source</dt>' +
            '      <dd class="muted">' + esc(src) + '</dd>' +
            '    </dl>' +
                compDots(asking, res.raw_comps) +
            '  </details>' +
            '</div>';

        return summaryHTML;
    };
})();
