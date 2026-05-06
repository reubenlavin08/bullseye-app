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

    // --- debug panel ----------------------------------------------------
    //
    // The user (during build-out) wants to see EVERY step the appraiser
    // took for any given listing: which canonical_kind the LLM picked,
    // which eBay search ran, every raw comp with title+price+URL,
    // whether the bimodal split fired (and which cluster won), which
    // prices Tukey trimmed as outliers, and the full score-formula
    // inputs and outputs.
    //
    // This expander is opt-in via the "Debug appraisal" <details>;
    // collapsed by default so non-debug surfaces don't visually bloat.
    // Once we're confident the pipeline is right we can hide via CSS
    // class — the data is always emitted by /appraise so toggling
    // visibility is a 0-line code change.

    function fmtNum(v, dp) {
        if (v == null || !isFinite(v)) return "—";
        var n = Number(v);
        if (dp == null) return String(n);
        return n.toFixed(dp);
    }

    function priceList(arr, max) {
        if (!arr || !arr.length) return '<span class="muted">none</span>';
        max = max || 50;
        var head = arr.slice(0, max);
        var rest = arr.length - head.length;
        var s = head.map(function (p) { return esc(money(p)); }).join(", ");
        if (rest > 0) s += " <span class=\"muted\">(+" + rest + " more)</span>";
        return s;
    }

    /** Render the full comp table — title, price, location, link out. */
    function rawCompsTable(comps) {
        if (!comps || !comps.length) {
            return '<p class="muted">No comps returned.</p>';
        }
        var rows = comps.map(function (c, i) {
            var t = c.title || c.item_id || "(untitled)";
            var p = c.price != null ? money(c.price) : "—";
            var loc = c.location || "";
            var url = c.listing_url || "";
            var linkOut = url
                ? '<a href="' + esc(url) + '" target="_blank" rel="noopener" class="muted">view ↗</a>'
                : '';
            return '<tr>' +
                '<td>' + (i + 1) + '</td>' +
                '<td>' + esc(t) + '</td>' +
                '<td class="num">' + esc(p) + '</td>' +
                '<td class="muted">' + esc(loc) + '</td>' +
                '<td>' + linkOut + '</td>' +
                '</tr>';
        }).join("");
        return '<table class="sc-debug-table">' +
            '<thead><tr><th>#</th><th>Title</th><th class="num">Price</th>' +
            '<th>Location</th><th></th></tr></thead>' +
            '<tbody>' + rows + '</tbody></table>';
    }

    function bimodalBlock(b) {
        if (!b) return '';
        if (!b.split_triggered) {
            return '<div class="sc-debug-row">' +
                '<span class="sc-debug-k">Bimodal split:</span> ' +
                '<span>not triggered</span>' +
                '</div>';
        }
        return '<div class="sc-debug-row">' +
            '<span class="sc-debug-k">Bimodal split:</span> ' +
            'TRIGGERED at ' + esc(money(b.split_at)) + '. ' +
            'Left cluster (' + b.left_size + ' items, median ' +
            esc(money(b.left_median)) + ') | ' +
            'Right cluster (' + b.right_size + ' items, median ' +
            esc(money(b.right_median)) + '). ' +
            'KEPT ' + esc(b.chose) + '; dropped ' + b.dropped_other_cluster +
            ' from the other cluster.' +
            '</div>';
    }

    function tukeyBlock(t) {
        if (!t) return '<div class="sc-debug-row">' +
            '<span class="sc-debug-k">Tukey trim:</span> ' +
            '<span class="muted">skipped (cluster &lt; 4 items)</span>' +
            '</div>';
        return '<div class="sc-debug-row">' +
            '<span class="sc-debug-k">Tukey fences:</span> ' +
            '[' + esc(money(t.lo_fence)) + ', ' + esc(money(t.hi_fence)) +
            ']  Q1=' + esc(money(t.q1)) + ' Q3=' + esc(money(t.q3)) +
            ' IQR=' + esc(money(t.iqr)) +
            '<br><span class="sc-debug-k">Kept after Tukey:</span> ' +
            priceList(t.kept_prices) +
            '<br><span class="sc-debug-k">Dropped (outliers):</span> ' +
            priceList(t.dropped_prices) +
            '</div>';
    }

    function normalizeBlock(n) {
        if (!n) return '<div class="sc-debug-row">' +
            '<span class="sc-debug-k">LLM normalize:</span> ' +
            '<span class="muted">skipped (no listing_url provided)</span>' +
            '</div>';
        if (n.is_fallback) {
            return '<div class="sc-debug-row">' +
                '<span class="sc-debug-k">LLM normalize:</span> ' +
                '<span class="muted">fallback (cloud unreachable; using raw title)</span>' +
                '</div>';
        }
        var cacheLabel = n.cache_hit ? "cache HIT" : "cache MISS (live LLM call)";
        return '<div class="sc-debug-row">' +
            '<span class="sc-debug-k">LLM normalize:</span> ' + esc(cacheLabel) +
            '<br><span class="sc-debug-k">canonical_kind:</span> <strong>' +
                esc(n.canonical_kind || "(empty)") + '</strong>' +
            '<br><span class="sc-debug-k">category_hint:</span> ' +
                esc(n.category_hint || "(missing)") +
            '<br><span class="sc-debug-k">coarse range:</span> ' +
                esc(money(n.coarse_low)) + ' – ' + esc(money(n.coarse_high)) +
            '<br><span class="sc-debug-k">confidence:</span> ' + esc(n.confidence) +
            '<br><span class="sc-debug-k">worth_deep:</span> ' + (n.worth_deep ? "true" : "FALSE — listing flagged as low-quality") +
            '<br><span class="sc-debug-k">red_flags:</span> ' +
                (n.red_flags && n.red_flags.length
                    ? esc(n.red_flags.join(", "))
                    : '<span class="muted">none</span>') +
            (n.reasoning
                ? '<br><span class="sc-debug-k">reasoning:</span> ' +
                  '<em class="muted">' + esc(n.reasoning) + '</em>'
                : '') +
            '</div>';
    }

    function compFiltersBlock(cf) {
        if (!cf) return '';
        var pb = cf.price_band;
        return '<div class="sc-debug-row">' +
            '<span class="sc-debug-k">eBay categoryId:</span> ' +
                (cf.category_id
                    ? '<code>' + esc(String(cf.category_id)) + '</code> ' +
                      '<span class="muted">(from hint "' + esc(cf.category_hint || "?") + '")</span>'
                    : '<span class="muted">none — using keyword + price band only</span>') +
            '<br><span class="sc-debug-k">price band:</span> ' +
                (pb && (pb.min || pb.max)
                    ? esc(money(pb.min)) + ' – ' + esc(money(pb.max))
                    : '<span class="muted">none</span>') +
            '</div>';
    }

    function scoreInputsBlock(si) {
        if (!si) return '';
        return '<div class="sc-debug-row">' +
            '<span class="sc-debug-k">Score formula trace:</span><br>' +
            'asking <code>' + fmtNum(si.asking_price) + '</code> ' +
            '/ trimmed_median <code>' + fmtNum(si.trimmed_median, 0) + '</code> ' +
            '= ratio <code>' + fmtNum(si.asking_price / Math.max(1, si.trimmed_median), 2) + '</code><br>' +
            'percentile_rank <code>' + fmtNum(si.percentile_rank, 3) + '</code> ' +
            '→ raw_score <code>' + fmtNum(si.raw_score_pre_condition, 1) + '</code><br>' +
            'confidence_pm pre-normalize <code>' + fmtNum(si.confidence_pm_pre_normalize) + '</code> ' +
            '+ normalize widening <code>' + fmtNum(si.normalize_widening) + '</code> ' +
            '= confidence_pm <code>' + fmtNum(si.confidence_pm_final) + '</code><br>' +
            'deal_score pre-cap <code>' + fmtNum(si.deal_score_pre_cap, 1) + '</code> ' +
            'capped at <code>(100 − confidence_pm) = ' + fmtNum(100 - si.confidence_pm_final, 1) + '</code> ' +
            '→ FINAL <code>' + fmtNum(si.deal_score_after_cap) + '/100</code>' +
            '</div>';
    }

    function debugPanel(res, opts) {
        if (!res || !res.debug) return "";
        opts = opts || {};
        var d = res.debug;
        var st = d.stats_trace || {};
        return '<details class="sc-debug"' + (opts.open ? " open" : "") + '>' +
            '<summary class="sc-summary">' +
                '<span class="sc-summary-label">Debug appraisal · every step</span>' +
                '<span class="sc-chev" aria-hidden="true">&#9656;</span>' +
            '</summary>' +
            '<div class="sc-debug-body">' +

                '<div class="sc-debug-section">' +
                '<h5>1. eBay search query</h5>' +
                '<div class="sc-debug-row">' +
                'Used: <strong>"' + esc(d.search_term_used || "") + '"</strong> ' +
                '<span class="muted">(source: ' + esc(d.search_term_source || "?") + ')</span><br>' +
                'Raw title: <span class="muted">' + esc(d.search_term_raw || "") + '</span>' +
                '</div></div>' +

                '<div class="sc-debug-section">' +
                '<h5>2. LLM normalize result</h5>' +
                normalizeBlock(d.normalize) +
                '</div>' +

                '<div class="sc-debug-section">' +
                '<h5>2b. eBay comp filters</h5>' +
                compFiltersBlock(d.comp_filters) +
                '</div>' +

                '<div class="sc-debug-section">' +
                '<h5>3. Comp set ' +
                '<span class="muted">(' + (st.input_count || 0) + ' raw comps)</span></h5>' +
                rawCompsTable(res.raw_comps) +
                '</div>' +

                '<div class="sc-debug-section">' +
                '<h5>4. Stats pipeline</h5>' +
                bimodalBlock(st.bimodal) +
                '<div class="sc-debug-row">' +
                '<span class="sc-debug-k">Active cluster (after bimodal):</span> ' +
                priceList(st.cluster_prices) +
                '</div>' +
                tukeyBlock(st.tukey) +
                '<div class="sc-debug-row">' +
                '<span class="sc-debug-k">Final stats:</span> ' +
                'median ' + esc(money(st.final && st.final.median)) +
                ' · trimmed_median ' + esc(money(st.final && st.final.trimmed_median)) +
                ' · trimmed_n ' + (st.final && st.final.trimmed_n != null ? st.final.trimmed_n : "—") +
                ' · outliers_dropped ' + (st.final && st.final.outliers_dropped != null ? st.final.outliers_dropped : "—") +
                '</div>' +
                '</div>' +

                (d.score_inputs
                    ? '<div class="sc-debug-section">' +
                      '<h5>5. Score formula</h5>' +
                      scoreInputsBlock(d.score_inputs) +
                      '</div>'
                    : '') +

                (d.guard_fired
                    ? '<div class="sc-debug-section">' +
                      '<h5>5. Sanity guard</h5>' +
                      '<div class="sc-debug-row">' +
                      'Guard <code>' + esc(d.guard_fired) + '</code> fired. ' +
                      'median_for_check = ' + esc(money(d.median_for_check)) +
                      '</div></div>'
                    : '') +

            '</div>' +
            '</details>';
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

    /**
     * Vague-listing warning. Triggered when the listing has so little
     * to work with that the appraisal is structurally unreliable —
     * not a comps problem, a SOURCE-DATA problem the user can't fix
     * by re-appraising. Heuristics:
     *   - canonical_kind <= 2 tokens AND no body fetched, OR
     *   - LLM normalize returned low confidence
     *   - LLM didn't run at all (no listing_url provided)
     *
     * Surfaces as a small amber banner with a "Fetch description"
     * hint so the user knows what would help.
     */
    function vagueListingWarning(res) {
        if (!res || res.unscoreable) return "";
        var ck = (res.canonical_kind || "").trim();
        var tokens = ck ? ck.split(/\s+/).filter(Boolean).length : 0;
        var lowConf = res.normalize_confidence === "low";
        var noNormalize = !res.canonical_kind && !res.normalize_confidence;
        // Only warn when one of the strong signals is present —
        // false positives here would just nag the user.
        var vague = (tokens > 0 && tokens <= 2) || lowConf || noNormalize;
        if (!vague) return "";
        var why = noNormalize
            ? "no listing description to work with"
            : (lowConf
                ? "very thin listing data"
                : '"' + esc(ck) + '" is too generic — many things match this');
        return '<div class="sc-vague-warn">' +
            '<strong>Limited info.</strong> ' +
            '<span>' + why + '. Appraisals on vague listings ' +
            'are inherently lower-confidence — try the ' +
            '<em>See description</em> button to pull the full ' +
            'listing body and re-score.</span>' +
            '</div>';
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

        // Even when unscoreable, show the same breakdown grid the
        // scoreable card shows — the user wants to see asking price,
        // attempted fair value, comp median, sample size, source. They
        // can use this to figure out what went wrong (e.g. comp median
        // way below asking → eBay returned mostly parts).
        var asking = Number(res.asking_price);
        var median = Number(res.median != null ? res.median : res.trimmed_median);
        var n = Number(res.sample_size) || (res.raw_comps || []).length;
        var src = res.comp_source || "—";
        var breakdown = '' +
            '<dl class="sc-grid">' +
            '  <dt>Asking price</dt>' +
            '  <dd>' + esc(money(asking)) + '</dd>' +
            (isFinite(median) && median > 0
                ? '  <dt>Comp median</dt>' +
                  '  <dd>' + esc(money(median)) + '</dd>'
                : '') +
            '  <dt>Sample size</dt>' +
            '  <dd>' + n + ' comp(s)</dd>' +
            '  <dt>Source</dt>' +
            '  <dd class="muted">' + esc(src) + '</dd>' +
            '</dl>';

        return '<div class="sc-card sc-unscoreable' +
                (isLowQuality ? ' sc-low-quality' : '') + '">' +
            appraisedAsHTML(res) +
            '<div class="sc-uns-head">' +
            '  <strong>' + esc(headStrong) + '</strong>' +
            '  <span class="muted"> · ' + esc(headSub) + '</span>' +
            '</div>' +
            redFlagsHTML(res.red_flags) +
            // Prominent breakdown — visible without expanding anything.
            // For unscoreable cards this is the most actionable info
            // the user has, so it goes ABOVE the debug fold.
            (isLowQuality ? "" : breakdown) +
            // Auto-open the debug panel on unscoreable cards so the
            // user can see WHY it failed (which filters fired, which
            // comps came back, where the bimodal split landed) without
            // having to hunt for the expander.
            debugPanel(res, { open: !isLowQuality }) +
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
            vagueListingWarning(res) +
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
            debugPanel(res) +
            '</div>';

        return summaryHTML;
    };
})();
