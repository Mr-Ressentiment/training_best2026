/* OrderShield draft advisory presentation component.
 *
 * Pure presentation-only renderer: renders server-returned advisory payloads handed
 * to it by app.js. Never fetches, re-evaluates, re-sorts, or recalculates business logic.
 * Every supplied value is untrusted display text and written through textContent only.
 *
 * Public API:
 *   OrderShieldAdvisoryPanel.render({ container, payload, supportedSchemaVersion, onFocusLine, onOpenEvidence, isStale })
 *   OrderShieldAdvisoryPanel.clear(container)
 *   OrderShieldAdvisoryPanel.isRendered(container)
 */
(function () {
  "use strict";

  if (window.OrderShieldAdvisoryPanel) {
    return;
  }

  var PLACEHOLDER = "—";
  var SUPPORTED_SCHEMA_VERSION = "1.0.0";

  var ADVISORY_STATUS_CONFIG = {
    "ready_no_action_needed": {
      label: "Ready (No Action Needed)",
      badgeClass: "badge--ok"
    },
    "actionable_plans_found": {
      label: "Actionable Plans Found",
      badgeClass: "badge--advisory"
    },
    "blocked_no_internal_plan": {
      label: "Blocked (No Internal Plan)",
      badgeClass: "badge--bad"
    },
    "requires_external_input": {
      label: "Requires External PO Revision",
      badgeClass: "badge--warn"
    },
    "not_applicable_terminal": {
      label: "Not Applicable (Terminal Draft)",
      badgeClass: "badge--neutral"
    }
  };

  var PRIORITY_BADGES = {
    "low": "badge--ok",
    "medium": "badge--warn",
    "high": "badge--bad"
  };

  var OUTCOME_BADGES = {
    "ok": "badge--ok",
    "blocking": "badge--bad",
    "warning": "badge--warn",
    "info": "badge--neutral",
    "requires_external_input": "badge--warn",
    "action_simulated": "badge--advisory",
    "ready": "badge--ready",
    "blocked": "badge--bad",
    "invalid": "badge--bad"
  };

  // ---------- Safe DOM helpers ----------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function isObject(val) {
    return val !== null && typeof val === "object" && !Array.isArray(val);
  }

  function isMissing(val) {
    return val === null || val === undefined || val === "";
  }

  function formatScore(val) {
    if (typeof val === "number" && !isNaN(val)) {
      return val.toFixed(4);
    }
    return isMissing(val) ? PLACEHOLDER : String(val);
  }

  function badge(label, modifier) {
    var span = el("span", "badge" + (modifier ? " " + modifier : ""), label);
    return span;
  }

  function advisoryBadge(label) {
    return badge(label, "badge--advisory");
  }

  function kvRow(list, term, valueText, isCode) {
    list.appendChild(el("dt", "", term));
    var dd = el("dd");
    if (isCode) {
      dd.appendChild(el("code", "advisory__code", isMissing(valueText) ? PLACEHOLDER : String(valueText)));
    } else {
      dd.textContent = isMissing(valueText) ? PLACEHOLDER : String(valueText);
      if (isMissing(valueText)) {
        dd.classList.add("placeholder");
      }
    }
    list.appendChild(dd);
  }

  // ---------- Meta Renderer ----------

  function renderMeta(container, payload, supportedSchemaVersion, isStale) {
    container.replaceChildren();

    var versionExpected = supportedSchemaVersion || SUPPORTED_SCHEMA_VERSION;
    if (payload.schema_version !== versionExpected) {
      var warnBox = el("div", "advisory__warning advisory__warning--schema",
        "Schema version mismatch: client expects " + versionExpected + ", server returned " +
        String(payload.schema_version) + ". Advisory data is displayed with defensive fallback.");
      container.appendChild(warnBox);
    }

    if (isStale === true) {
      var staleBox = el("div", "advisory__warning advisory__warning--stale",
        "Draft was modified after this advisory evaluation was calculated. Click Reload advisory for updated recommendations.");
      container.appendChild(staleBox);
    }

    var head = el("div", "advisory__subhead");
    head.appendChild(el("h4", "", "Evaluation Overview"));
    container.appendChild(head);

    var grid = el("div", "advisory__grid");

    // Card 1: Status & Contract Metadata
    var cardMeta = el("div", "advisory__card");
    cardMeta.appendChild(el("h5", "advisory__card-title", "Status & Contract"));
    var dlMeta = el("dl", "advisory__kv");

    kvRow(dlMeta, "Schema version", payload.schema_version, true);

    var rawStatus = payload.advisory_status;
    var statusCfg = ADVISORY_STATUS_CONFIG[rawStatus];
    var statusLabel = statusCfg ? statusCfg.label : String(rawStatus || PLACEHOLDER);
    var statusBadgeMod = statusCfg ? statusCfg.badgeClass : "badge--neutral";

    dlMeta.appendChild(el("dt", "", "Advisory status"));
    var ddStatus = el("dd");
    ddStatus.appendChild(badge(statusLabel, statusBadgeMod));
    if (rawStatus) {
      ddStatus.appendChild(el("span", "advisory__tag", rawStatus));
    }
    dlMeta.appendChild(ddStatus);

    kvRow(dlMeta, "Stored status", payload.status_persisted);
    kvRow(dlMeta, "Included sections", Array.isArray(payload.included_sections) ? payload.included_sections.join(", ") : PLACEHOLDER);
    kvRow(dlMeta, "Omitted sections", Array.isArray(payload.sections_omitted) && payload.sections_omitted.length > 0 ? payload.sections_omitted.join(", ") : "none");

    cardMeta.appendChild(dlMeta);
    grid.appendChild(cardMeta);

    // Card 2: Baseline State & In-Memory Sandbox Comparison
    if (isObject(payload.baseline)) {
      var cardBaseline = el("div", "advisory__card");
      cardBaseline.appendChild(el("h5", "advisory__card-title", "Baseline Reconciliation"));
      var dlBase = el("dl", "advisory__kv");

      kvRow(dlBase, "Stored status", payload.baseline.status_persisted);
      kvRow(dlBase, "Fresh simulation", payload.baseline.simulated_status || "(terminal / none)");

      dlBase.appendChild(el("dt", "", "Evaluation freshness"));
      var ddFresh = el("dd");
      if (payload.baseline.is_stale_evaluation === true) {
        ddFresh.appendChild(badge("stored status is stale", "badge--warn"));
      } else {
        ddFresh.textContent = "Evaluation matches fresh simulation";
      }
      dlBase.appendChild(ddFresh);

      if (isObject(payload.baseline.order_arithmetic)) {
        var arith = payload.baseline.order_arithmetic;
        kvRow(dlBase, "Stored subtotal", arith.persisted_subtotal);
        kvRow(dlBase, "Calculated subtotal", arith.calculated_subtotal);

        dlBase.appendChild(el("dt", "", "Extracted order total"));
        var ddTot = el("dd");
        if (arith.extracted_order_total !== null && arith.extracted_order_total !== undefined) {
          ddTot.textContent = String(arith.extracted_order_total);
        } else {
          ddTot.textContent = PLACEHOLDER;
          ddTot.classList.add("placeholder");
          if (arith.extracted_order_total_missing_reason) {
            ddTot.appendChild(el("span", "advisory__subtext", " (" + arith.extracted_order_total_missing_reason + ")"));
          }
        }
        dlBase.appendChild(ddTot);
      }

      cardBaseline.appendChild(dlBase);
      grid.appendChild(cardBaseline);
    }

    // Card 3: Counterfactual Search Space & Coverage
    if (isObject(payload.counterfactuals)) {
      var cf = payload.counterfactuals;
      var cardCf = el("div", "advisory__card");
      cardCf.appendChild(el("h5", "advisory__card-title", "Search Bounds & Coverage"));
      var dlCf = el("dl", "advisory__kv");

      if (isObject(cf.limits)) {
        kvRow(dlCf, "Max sequence depth", cf.limits.max_depth);
        kvRow(dlCf, "Max scenarios budget", cf.limits.max_scenarios);
      }
      if (isObject(cf.coverage)) {
        kvRow(dlCf, "Scenarios simulated", cf.coverage.total_scenarios_evaluated);
        kvRow(dlCf, "Search exhaustive", cf.coverage.search_is_exhaustive ? "Yes (full search within bounds)" : "No");

        dlCf.appendChild(el("dt", "", "Truncation"));
        var ddTrunc = el("dd");
        if (cf.coverage.is_truncated === true) {
          ddTrunc.appendChild(badge("search truncated", "badge--warn"));
          ddTrunc.appendChild(el("span", "advisory__subtext", " Search ended early due to max scenarios limit"));
        } else {
          ddTrunc.textContent = "Completed within limit";
        }
        dlCf.appendChild(ddTrunc);
      }

      cardCf.appendChild(dlCf);

      if (isObject(cf.coverage) && cf.coverage.interpretation_note) {
        cardCf.appendChild(el("p", "advisory__note", cf.coverage.interpretation_note));
      }

      grid.appendChild(cardCf);
    }

    container.appendChild(grid);
  }

  // ---------- Review Priority Renderer ----------

  function renderPriority(container, payload) {
    container.replaceChildren();

    if (!isObject(payload.review_priority)) {
      return;
    }

    var rp = payload.review_priority;
    var head = el("div", "advisory__subhead");
    head.appendChild(el("h4", "", "Review Priority Evaluation"));
    var lbl = rp.review_priority_label || "";
    head.appendChild(badge("Priority: " + (lbl ? lbl.toUpperCase() : PLACEHOLDER), PRIORITY_BADGES[lbl] || "badge--neutral"));
    container.appendChild(head);

    var grid = el("div", "advisory__grid");

    // Primary score card
    var cardScore = el("div", "advisory__card");
    cardScore.appendChild(el("h5", "advisory__card-title", "Priority Score & Invariants"));
    var dlScore = el("dl", "advisory__kv");

    dlScore.appendChild(el("dt", "", "Review priority score"));
    var ddScore = el("dd", "advisory__highlight-val");
    ddScore.appendChild(el("strong", "", formatScore(rp.review_priority)));
    ddScore.appendChild(el("span", "advisory__subtext", " [range: 0.0 – 1.0; heuristic score, not a probability]"));
    dlScore.appendChild(ddScore);

    kvRow(dlScore, "Linguistic tier", rp.review_priority_label ? rp.review_priority_label.toUpperCase() : PLACEHOLDER);
    kvRow(dlScore, "Raw Mamdani score", formatScore(rp.raw_fuzzy_score));

    dlScore.appendChild(el("dt", "", "Monotonic adjustment"));
    var ddMono = el("dd");
    if (rp.monotonic_adjustment === true) {
      ddMono.appendChild(badge("monotonic adjustment applied", "badge--warn"));
      ddMono.appendChild(el("span", "advisory__subtext", " Raw score raised by upper envelope to preserve monotonicity"));
    } else {
      ddMono.textContent = "None (raw score satisfies monotonicity)";
    }
    dlScore.appendChild(ddMono);

    dlScore.appendChild(el("dt", "", "Data completeness"));
    var ddComp = el("dd");
    if (rp.has_incomplete_data === true) {
      ddComp.appendChild(badge("incomplete inputs", "badge--warn"));
      ddComp.appendChild(el("span", "advisory__subtext", " Safety floor applied for missing features"));
    } else {
      ddComp.textContent = "All features present";
    }
    dlScore.appendChild(ddComp);

    if (Array.isArray(rp.applied_rules)) {
      kvRow(dlScore, "Rules fired (" + rp.applied_rules.length + ")", rp.applied_rules.join(", "), true);
    }

    cardScore.appendChild(dlScore);
    grid.appendChild(cardScore);

    // Inputs transparency card
    if (isObject(rp.inputs_used)) {
      var inp = rp.inputs_used;
      var cardInp = el("div", "advisory__card");
      cardInp.appendChild(el("h5", "advisory__card-title", "Evaluator Inputs Transparency"));
      var dlInp = el("dl", "advisory__kv");

      kvRow(dlInp, "Discrepancy count", inp.discrepancy_count);
      kvRow(dlInp, "Discrepancy severity", formatScore(inp.discrepancy_severity));
      kvRow(dlInp, "Price deviation %", isMissing(inp.price_deviation_pct) ? PLACEHOLDER : inp.price_deviation_pct + "%");
      kvRow(dlInp, "Order total", inp.order_total_formatted || (isMissing(inp.order_total_cents) ? PLACEHOLDER : "$" + (inp.order_total_cents / 100).toFixed(2)));
      kvRow(dlInp, "Normalized total", formatScore(inp.normalized_order_total));

      dlInp.appendChild(el("dt", "", "SKU match confidence"));
      var ddConf = el("dd");
      ddConf.textContent = inp.match_confidence !== null && inp.match_confidence !== undefined
        ? formatScore(inp.match_confidence)
        : PLACEHOLDER;

      if (inp.match_confidence_source === "mapped_from_label") {
        ddConf.appendChild(el("span", "advisory__tag", "mapped from label · heuristic, not calibrated"));
      } else if (inp.match_confidence_source) {
        ddConf.appendChild(el("span", "advisory__tag", inp.match_confidence_source));
      }
      dlInp.appendChild(ddConf);

      kvRow(dlInp, "Persisted SKU label", inp.persisted_match_confidence_label);
      kvRow(dlInp, "Effective uncertainty", formatScore(inp.effective_uncertainty));

      cardInp.appendChild(dlInp);

      if (inp.note) {
        cardInp.appendChild(el("p", "advisory__note", inp.note));
      }

      grid.appendChild(cardInp);
    }

    container.appendChild(grid);
  }

  // ---------- Action Hints Renderer ----------

  function renderHints(container, payload, onFocusLine) {
    container.replaceChildren();

    var head = el("div", "advisory__subhead");
    head.appendChild(el("h4", "", "Action Hints"));
    head.appendChild(advisoryBadge("simulated · advisory only"));
    container.appendChild(head);

    var subtext = el("p", "advisory__subtext",
      "These hints represent in-memory simulated dry-runs in an isolated sandbox. " +
      "They do not alter the current draft, execute commercial actions, or override approval gates.");
    container.appendChild(subtext);

    var status = payload.advisory_status;
    var cf = isObject(payload.counterfactuals) ? payload.counterfactuals : null;

    if (status === "not_applicable_terminal") {
      var termMsg = el("div", "advisory__status-card advisory__status-card--neutral");
      termMsg.appendChild(el("p", "",
        "Advisory analysis does not apply to drafts in terminal status (" +
        (payload.status_persisted || "Approved/Rejected") + ")."));
      container.appendChild(termMsg);
      return;
    }

    if (status === "ready_no_action_needed") {
      var readyMsg = el("div", "advisory__status-card advisory__status-card--ok");
      readyMsg.appendChild(el("p", "", "No operator action required: this draft already reconciles to Ready for Approval."));
      container.appendChild(readyMsg);
      return;
    }

    if (status === "actionable_plans_found") {
      var plans = cf && Array.isArray(cf.successful_plans) ? cf.successful_plans : [];
      if (plans.length === 0) {
        container.appendChild(el("p", "muted", "The server returned no plans."));
        return;
      }

      var plansList = el("div", "advisory__plans");
      plans.forEach(function (plan, idx) {
        var planCard = el("article", "advisory__plan-card");

        var planHead = el("div", "advisory__plan-head");
        planHead.appendChild(el("span", "advisory__plan-idx", "Plan " + (idx + 1)));
        planHead.appendChild(el("code", "mono-id", plan.plan_id));
        planHead.appendChild(advisoryBadge("simulated · advisory only"));
        planHead.appendChild(badge("Simulated outcome: " + (plan.simulated_status || plan.outcome), "badge--ready"));
        planCard.appendChild(planHead);

        if (plan.explanation) {
          planCard.appendChild(el("p", "advisory__plan-explanation", plan.explanation));
        }

        var actionsWrap = el("div", "advisory__plan-actions");
        actionsWrap.appendChild(el("h5", "advisory__plan-actions-title", "Simulated actions:"));
        var ol = el("ol", "advisory__action-list");

        var actions = Array.isArray(plan.actions) ? plan.actions : [];
        actions.forEach(function (act) {
          var li = el("li", "advisory__action-item");
          li.appendChild(el("span", "advisory__action-type", act.action_type));

          if (act.line_number !== null && act.line_number !== undefined) {
            var lineBtn = el("button", "btn btn--small btn--outline advisory__line-btn", "Line " + act.line_number);
            lineBtn.type = "button";
            lineBtn.title = "Scroll to draft line " + act.line_number;
            lineBtn.addEventListener("click", function () {
              if (typeof onFocusLine === "function") {
                onFocusLine(act.line_number);
              }
            });
            li.appendChild(lineBtn);
          }

          if (act.sku) {
            li.appendChild(el("span", "advisory__action-detail", "Selected SKU: "));
            li.appendChild(el("code", "advisory__code", act.sku));
          }

          if (act.reason) {
            li.appendChild(el("span", "advisory__subtext", " (" + act.reason + ")"));
          }

          ol.appendChild(li);
        });

        actionsWrap.appendChild(ol);
        planCard.appendChild(actionsWrap);
        plansList.appendChild(planCard);
      });

      container.appendChild(plansList);
      return;
    }

    if (status === "blocked_no_internal_plan" || status === "requires_external_input") {
      var blockCard = el("div", "advisory__status-card advisory__status-card--warn");
      blockCard.appendChild(el("h5", "", "Internal operator actions cannot achieve Ready for Approval"));

      if (cf && isObject(cf.requires_external_input) && cf.requires_external_input.is_required) {
        var ext = cf.requires_external_input;
        var extBox = el("div", "advisory__callout advisory__callout--warn");
        extBox.appendChild(el("strong", "", "Customer correction required: "));
        extBox.appendChild(el("span", "",
          "A corrected Purchase Order must be requested from the customer to resolve commercial discrepancies."));

        if (Array.isArray(ext.reasons) && ext.reasons.length > 0) {
          extBox.appendChild(el("p", "advisory__subtext", "Reasons: " + ext.reasons.join(", ")));
        }
        if (ext.explanation) {
          extBox.appendChild(el("p", "advisory__explanation", ext.explanation));
        }
        blockCard.appendChild(extBox);
      }

      // Highlight baseline blockers
      if (isObject(payload.baseline) && Array.isArray(payload.baseline.blockers) && payload.baseline.blockers.length > 0) {
        var blkWrap = el("div", "advisory__blockers");
        blkWrap.appendChild(el("h5", "advisory__blockers-title", "Active Baseline Blockers:"));
        var bUl = el("ul", "advisory__blockers-list");

        payload.baseline.blockers.forEach(function (blk) {
          var bLi = el("li");
          var lineDesc = blk.line_number ? "Line " + blk.line_number + ": " : "Order-level: ";
          bLi.appendChild(el("strong", "", lineDesc + blk.discrepancy_type + " (" + blk.severity + ") — "));
          bLi.appendChild(el("span", "", "expected " + blk.expected_value + ", requested " + blk.requested_value + ". "));
          if (blk.explanation) {
            bLi.appendChild(el("span", "muted", blk.explanation));
          }
          if (isObject(blk.price_source)) {
            var ps = blk.price_source;
            var psInfo = " [Contract: " + ps.contract_id + ", Tier: " + ps.tier_id + ", Min Qty: " + ps.min_quantity + ", Price: $" + ps.tier_price + "]";
            bLi.appendChild(el("span", "advisory__subtext", psInfo));
          }
          bUl.appendChild(bLi);
        });

        blkWrap.appendChild(bUl);
        blockCard.appendChild(blkWrap);
      }

      container.appendChild(blockCard);
      return;
    }

    // Fallback for any unknown status
    container.appendChild(el("p", "muted", "The server returned no plans."));
  }

  // ---------- SKU Confidence Renderer ----------

  function renderConfidence(container, payload, onFocusLine) {
    container.replaceChildren();

    if (!isObject(payload.sku_confidence)) {
      return;
    }

    var sc = payload.sku_confidence;
    var lines = Array.isArray(sc.lines) ? sc.lines : [];

    var head = el("div", "advisory__subhead");
    head.appendChild(el("h4", "", "SKU Match Confidence"));
    head.appendChild(el("span", "muted", lines.length + (lines.length === 1 ? " line evaluated" : " lines evaluated")));
    container.appendChild(head);

    if (lines.length === 0) {
      container.appendChild(el("p", "muted", "No SKU confidence evaluations returned."));
      return;
    }

    var tableWrap = el("div", "table-wrap");
    var table = el("table", "lines advisory__table");
    var thead = el("thead");
    var trHead = el("tr");

    trHead.appendChild(el("th", "num", "#"));
    trHead.appendChild(el("th", "", "Customer Description"));
    trHead.appendChild(el("th", "", "Matched SKU"));
    trHead.appendChild(el("th", "", "Persisted Label"));
    trHead.appendChild(el("th", "num", "Evaluated Score"));
    trHead.appendChild(el("th", "", "Evaluated Label"));
    trHead.appendChild(el("th", "", "Confidence Source"));
    trHead.appendChild(el("th", "num", "Score Margin"));
    trHead.appendChild(el("th", "", "Reason & Candidates"));

    thead.appendChild(trHead);
    table.appendChild(thead);

    var tbody = el("tbody");
    lines.forEach(function (ln) {
      var tr = el("tr");

      // Line number with focus button
      var tdNum = el("td", "num");
      if (ln.line_number !== null && ln.line_number !== undefined) {
        var numBtn = el("button", "btn btn--small btn--outline advisory__line-btn", String(ln.line_number));
        numBtn.type = "button";
        numBtn.title = "Scroll to draft line " + ln.line_number;
        numBtn.addEventListener("click", function () {
          if (typeof onFocusLine === "function") {
            onFocusLine(ln.line_number);
          }
        });
        tdNum.appendChild(numBtn);
      } else {
        tdNum.textContent = PLACEHOLDER;
      }
      tr.appendChild(tdNum);

      // Customer Description
      tr.appendChild(el("td", "", ln.customer_description));

      // Matched SKU
      var tdSku = el("td");
      if (ln.matched_sku) {
        tdSku.appendChild(el("span", "sku", ln.matched_sku));
      } else {
        tdSku.textContent = PLACEHOLDER;
        tdSku.classList.add("placeholder");
      }
      tr.appendChild(tdSku);

      // Persisted Label
      var tdPers = el("td");
      if (ln.persisted_label) {
        tdPers.appendChild(badge(ln.persisted_label, PRIORITY_BADGES[ln.persisted_label.toLowerCase()] || "badge--neutral"));
      } else {
        tdPers.textContent = PLACEHOLDER;
        tdPers.classList.add("placeholder");
      }
      tr.appendChild(tdPers);

      // Evaluated Score
      tr.appendChild(el("td", "num", formatScore(ln.evaluated_confidence)));

      // Evaluated Label
      var tdEvLabel = el("td");
      if (ln.evaluated_label) {
        tdEvLabel.appendChild(badge(ln.evaluated_label, PRIORITY_BADGES[ln.evaluated_label.toLowerCase()] || "badge--neutral"));
      } else {
        tdEvLabel.textContent = PLACEHOLDER;
        tdEvLabel.classList.add("placeholder");
      }
      tr.appendChild(tdEvLabel);

      // Confidence Source
      var tdSrc = el("td");
      if (ln.confidence_source === "mapped_from_label") {
        tdSrc.appendChild(el("span", "", "mapped_from_label"));
        tdSrc.appendChild(el("span", "advisory__tag", "heuristic mapping, not measured probability"));
      } else {
        tdSrc.textContent = isMissing(ln.confidence_source) ? PLACEHOLDER : String(ln.confidence_source);
      }
      tr.appendChild(tdSrc);

      // Score Margin
      tr.appendChild(el("td", "num", formatScore(ln.score_margin)));

      // Reason & Candidates
      var tdReason = el("td", "advisory__reason-cell");
      if (ln.reason) {
        tdReason.appendChild(el("p", "advisory__reason-text", ln.reason));
      }
      if (ln.note) {
        tdReason.appendChild(el("p", "advisory__subtext", ln.note));
      }

      if (Array.isArray(ln.candidates) && ln.candidates.length > 0) {
        var candDetails = el("details", "advisory__candidates");
        var summary = el("summary", "", "Candidates (" + ln.candidates.length + ")");
        candDetails.appendChild(summary);

        var candList = el("ul", "advisory__candidates-list");
        ln.candidates.forEach(function (c) {
          var cLi = el("li");
          cLi.appendChild(el("code", "advisory__code", c.sku));
          cLi.appendChild(el("span", "", " score: " + formatScore(c.score) + " (" + (c.label || "") + ")"));
          cLi.appendChild(el("span", "advisory__subtext",
            " [desc_sim: " + formatScore(c.description_similarity) +
            ", price_close: " + formatScore(c.price_closeness) +
            ", qty_plaus: " + formatScore(c.quantity_plausibility) + "]"));
          candList.appendChild(cLi);
        });

        candDetails.appendChild(candList);
        tdReason.appendChild(candDetails);
      }

      tr.appendChild(tdReason);
      tbody.appendChild(tr);
    });

    table.appendChild(tbody);
    tableWrap.appendChild(table);
    container.appendChild(tableWrap);
  }

  // ---------- Derivation Trace Renderer ----------

  function renderTrace(container, payload, onFocusLine, onOpenEvidence) {
    container.replaceChildren();

    if (!isObject(payload.trace)) {
      return;
    }

    var steps = Array.isArray(payload.trace.steps) ? payload.trace.steps : [];

    var head = el("div", "advisory__subhead");
    head.appendChild(el("h4", "", "Derivation Trace"));
    head.appendChild(el("span", "muted", steps.length + (steps.length === 1 ? " decision step" : " decision steps") + " · server order"));
    container.appendChild(head);

    var note = el("p", "advisory__subtext",
      "Step-by-step decision sequence grounded in document provenance and contract price tier lookup.");
    container.appendChild(note);

    if (steps.length === 0) {
      container.appendChild(el("p", "muted", "No derivation trace steps returned."));
      return;
    }

    var ol = el("ol", "advisory__trace-list");

    steps.forEach(function (step) {
      var li = el("li", "advisory__trace-step");

      // Step header
      var stepHead = el("div", "advisory__trace-head");
      stepHead.appendChild(el("span", "advisory__step-num", "Step " + step.step_index));

      if (step.stage) {
        stepHead.appendChild(badge(step.stage, "badge--neutral"));
      }

      // Subject tag
      if (step.subject) {
        var subSpan = el("span", "advisory__subject-tag");
        if (step.subject.indexOf("line:") === 0) {
          var linePart = step.subject.replace("line:", "");
          var lineNum = parseInt(linePart, 10);
          if (!isNaN(lineNum)) {
            var subBtn = el("button", "btn btn--small btn--outline advisory__line-btn", "Line " + lineNum);
            subBtn.type = "button";
            subBtn.title = "Scroll to draft line " + lineNum;
            subBtn.addEventListener("click", function () {
              if (typeof onFocusLine === "function") {
                onFocusLine(lineNum);
              }
            });
            subSpan.appendChild(subBtn);
          } else {
            subSpan.textContent = step.subject;
          }
        } else {
          subSpan.textContent = step.subject;
        }
        stepHead.appendChild(subSpan);
      }

      if (step.rule_or_decision) {
        stepHead.appendChild(el("code", "advisory__code", step.rule_or_decision));
      }

      if (step.outcome) {
        var outMod = OUTCOME_BADGES[step.outcome] || "badge--neutral";
        stepHead.appendChild(badge(step.outcome, outMod));
      }

      li.appendChild(stepHead);

      // Explanation
      if (step.explanation) {
        li.appendChild(el("p", "advisory__trace-explanation", step.explanation));
      }

      // Inputs table / definition list
      if (isObject(step.inputs) && Object.keys(step.inputs).length > 0) {
        var inputsDetails = el("details", "advisory__trace-inputs");
        inputsDetails.appendChild(el("summary", "", "Inputs used (" + Object.keys(step.inputs).length + ")"));
        var dlInp = el("dl", "advisory__kv");
        Object.keys(step.inputs).forEach(function (k) {
          var v = step.inputs[k];
          var vStr = isObject(v) || Array.isArray(v) ? JSON.stringify(v) : (isMissing(v) ? PLACEHOLDER : String(v));
          kvRow(dlInp, k, vStr, typeof v === "object");
        });
        inputsDetails.appendChild(dlInp);
        li.appendChild(inputsDetails);
      }

      // Evidence list
      if (Array.isArray(step.evidence) && step.evidence.length > 0) {
        var evSection = el("div", "advisory__trace-evidence");
        evSection.appendChild(el("h5", "advisory__evidence-title", "Evidence citations:"));
        var evUl = el("ul", "advisory__evidence-list");

        step.evidence.forEach(function (ev) {
          var evLi = el("li", "advisory__evidence-item");

          if (ev.source_type === "document" && isObject(ev.document_evidence)) {
            var docEv = ev.document_evidence;
            evLi.classList.add("advisory__evidence-item--document");

            var docHead = el("div", "advisory__evidence-head");
            docHead.appendChild(el("span", "advisory__evidence-type", "Document source:"));
            docHead.appendChild(el("code", "advisory__code", docEv.field_name));

            var srcBtn = el("button", "btn btn--small btn--outline source-evidence-control", "Source");
            srcBtn.type = "button";
            srcBtn.setAttribute("aria-label", "Source evidence for " + docEv.field_name);
            srcBtn.addEventListener("click", function () {
              if (typeof onOpenEvidence === "function") {
                onOpenEvidence({
                  label: "Trace Step " + step.step_index + " · " + docEv.field_name,
                  value: docEv.verbatim_snippet,
                  provenance: {
                    field_name: docEv.field_name,
                    verbatim_snippet: docEv.verbatim_snippet,
                    location: docEv.location
                  }
                });
              }
            });
            docHead.appendChild(srcBtn);
            evLi.appendChild(docHead);

            if (docEv.verbatim_snippet) {
              evLi.appendChild(el("blockquote", "advisory__snippet", docEv.verbatim_snippet));
            }

            if (isObject(docEv.location)) {
              var loc = docEv.location;
              var locText = loc.type === "txt"
                ? "Line " + loc.line_number + ", offset " + loc.char_offset
                : (loc.type === "pdf" ? "Page " + loc.page_number + " (" + loc.char_start + "–" + loc.char_end + ")" : "Location provided");
              evLi.appendChild(el("span", "advisory__subtext", "Location: " + locText));
            }
          } else if (ev.source_type === "reference_data" && isObject(ev.reference_evidence)) {
            var refEv = ev.reference_evidence;
            evLi.classList.add("advisory__evidence-item--ref");

            evLi.appendChild(el("strong", "", "Catalog / Contract reference: "));
            evLi.appendChild(el("code", "advisory__code", refEv.sku));

            var refParts = [];
            if (refEv.contract_id) { refParts.push("Contract: " + refEv.contract_id); }
            if (refEv.tier_id) { refParts.push("Tier: " + refEv.tier_id); }
            if (refEv.min_quantity !== null && refEv.min_quantity !== undefined) { refParts.push("Min Qty: " + refEv.min_quantity); }
            if (refEv.tier_price) { refParts.push("Tier Price: $" + refEv.tier_price); }

            if (refParts.length > 0) {
              evLi.appendChild(el("span", "advisory__subtext", " (" + refParts.join(", ") + ")"));
            }
          } else if (ev.source_type === "derived" && isObject(ev.derived_evidence)) {
            var derEv = ev.derived_evidence;
            evLi.classList.add("advisory__evidence-item--derived");

            evLi.appendChild(el("strong", "", "Derived formula: "));
            evLi.appendChild(el("code", "advisory__code", derEv.formula));
            evLi.appendChild(el("span", "", " = " + String(derEv.result_value)));

            if (isObject(derEv.input_values)) {
              evLi.appendChild(el("span", "advisory__subtext", " (inputs: " + JSON.stringify(derEv.input_values) + ")"));
            }
          } else {
            evLi.appendChild(el("span", "", "Evidence [" + String(ev.source_type || "unknown") + "]"));
          }

          evUl.appendChild(evLi);
        });

        evSection.appendChild(evUl);
        li.appendChild(evSection);
      }

      ol.appendChild(li);
    });

    container.appendChild(ol);
  }

  // ---------- Public API ----------

  function clear(container) {
    if (!container) {
      return;
    }
    var metaNode = container.querySelector("#advisory-meta");
    var priorityNode = container.querySelector("#advisory-priority");
    var hintsNode = container.querySelector("#advisory-hints");
    var confNode = container.querySelector("#advisory-confidence");
    var traceNode = container.querySelector("#advisory-trace");

    if (metaNode) { metaNode.replaceChildren(); }
    if (priorityNode) { priorityNode.replaceChildren(); }
    if (hintsNode) { hintsNode.replaceChildren(); }
    if (confNode) { confNode.replaceChildren(); }
    if (traceNode) { traceNode.replaceChildren(); }
  }

  function isRendered(container) {
    if (!container) {
      return false;
    }
    var metaNode = container.querySelector("#advisory-meta");
    return metaNode !== null && metaNode.childElementCount > 0;
  }

  function render(options) {
    if (!options || !options.container) {
      return;
    }
    var container = options.container;
    var payload = options.payload;
    var supportedSchemaVersion = options.supportedSchemaVersion;
    var onFocusLine = options.onFocusLine;
    var onOpenEvidence = options.onOpenEvidence;
    var isStale = options.isStale;

    clear(container);

    if (!isObject(payload)) {
      return;
    }

    var metaNode = container.querySelector("#advisory-meta");
    var priorityNode = container.querySelector("#advisory-priority");
    var hintsNode = container.querySelector("#advisory-hints");
    var confNode = container.querySelector("#advisory-confidence");
    var traceNode = container.querySelector("#advisory-trace");

    if (metaNode) {
      renderMeta(metaNode, payload, supportedSchemaVersion, isStale);
    }
    if (priorityNode) {
      renderPriority(priorityNode, payload);
    }
    if (hintsNode) {
      renderHints(hintsNode, payload, onFocusLine);
    }
    if (confNode) {
      renderConfidence(confNode, payload, onFocusLine);
    }
    if (traceNode) {
      renderTrace(traceNode, payload, onFocusLine, onOpenEvidence);
    }
  }

  window.OrderShieldAdvisoryPanel = {
    render: render,
    clear: clear,
    isRendered: isRendered
  };
})();
