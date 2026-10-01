/* OrderShield P1/P2 workspace logic.
 *
 * The backend is authoritative for status, pricing, totals, readiness, SKU
 * resolution, discrepancy resolution, rejection and approval outcome. This
 * file only transports requests and renders response bodies; it never
 * recomputes business rules.
 *
 * LIVE   -> POST /api/v1/orders/ingest
 * REPLAY -> POST /api/v1/fixtures/{fixture_id}/ingest
 * The two paths are never substituted for each other and nothing is retried.
 *
 * P2 operator mutations (SelectSKU, line removal, rejection) each replace
 * state.draft with the server response and rerender; nothing is patched locally.
 */
(function () {
  "use strict";

  var API = "/api/v1";
  var PLACEHOLDER = "—";
  var READY_STATUS = "Ready for Approval";
  var TERMINAL_STATUSES = ["Approved", "Rejected"];
  var ACTIVE_LINE_STATUS = "Active";
  var UNRESOLVED_STATE = "Unresolved";
  var CATALOG_MODAL_SRC = "js/components/catalog_modal.js";
  var LIVE_EXTENSIONS = [".txt", ".pdf"];

  var STATUS_BADGES = {
    "Ready for Approval": "badge--ready",
    "Needs Review": "badge--review",
    "Approved": "badge--approved",
    "Rejected": "badge--rejected"
  };

  // ---------- API helper ----------

  function ApiError(context, status, error, message) {
    this.context = context;
    this.status = status;
    this.error = error;
    this.message = message;
  }

  /** Single request, no retry. Rejects with ApiError on network failure or non-2xx. */
  async function requestJson(context, path, options) {
    var response;
    try {
      response = await fetch(API + path, options || {});
    } catch (networkError) {
      throw new ApiError(context, null, "NetworkError", "Could not reach the OrderShield server. Check that it is running.");
    }
    var body = null;
    try {
      body = await response.json();
    } catch (parseError) {
      body = null;
    }
    if (!response.ok) {
      var error = body && typeof body.error === "string" ? body.error : "";
      var message = body && typeof body.message === "string" ? body.message : "";
      throw new ApiError(context, response.status, error, message || "The server returned an error without a diagnostic message.");
    }
    if (body === null) {
      throw new ApiError(context, response.status, "InvalidResponse", "The server response was not valid JSON.");
    }
    return body;
  }

  // ---------- State ----------

  var state = {
    draft: null,           // current draft (its draft_id and is_replay_mode are the current id / mode)
    approvedOrder: null,   // verified-order response for the current draft
    fixtures: [],
    selectedFixtureId: "",
    liveFile: null,
    loading: null,         // null | activity label while a request is pending
    error: null            // null | {context, status, error, message}
  };

  // ---------- DOM references ----------

  function $(id) {
    return document.getElementById(id);
  }

  var dom = {
    activity: $("activity"),
    replayBanner: $("replay-banner"),
    diagnostics: $("diagnostics"),
    diagnosticContext: $("diagnostic-context"),
    diagnosticStatus: $("diagnostic-status"),
    diagnosticError: $("diagnostic-error"),
    diagnosticMessage: $("diagnostic-message"),
    diagnosticDismiss: $("diagnostic-dismiss"),
    liveForm: $("live-form"),
    dropzone: $("dropzone"),
    liveFile: $("live-file"),
    liveFileName: $("live-file-name"),
    liveSubmit: $("live-submit"),
    fixtureForm: $("fixture-form"),
    fixtureSelect: $("fixture-select"),
    fixtureDescription: $("fixture-description"),
    fixtureSubmit: $("fixture-submit"),
    draftEmpty: $("draft-empty"),
    draftSection: $("draft-section"),
    draftId: $("draft-id"),
    summaryCustomer: $("summary-customer"),
    summaryCustomerId: $("summary-customer-id"),
    summaryPo: $("summary-po"),
    summaryStatus: $("summary-status"),
    summaryMode: $("summary-mode"),
    summarySubtotal: $("summary-subtotal"),
    lineCount: $("line-count"),
    lineRows: $("line-rows"),
    approveForm: $("approve-form"),
    operatorId: $("operator-id"),
    approveSubmit: $("approve-submit"),
    approvalHint: $("approval-hint"),
    resultSection: $("result-section"),
    resultMode: $("result-mode"),
    resultOrderNumber: $("result-order-number"),
    resultGrandTotal: $("result-grand-total"),
    resultPo: $("result-po"),
    resultCustomerId: $("result-customer-id"),
    resultApprovedBy: $("result-approved-by"),
    resultApprovedAt: $("result-approved-at"),
    resultLines: $("result-lines"),
    resultOrderId: $("result-order-id")
  };

  // ---------- Render helpers ----------

  function isMissing(value) {
    return value === null || value === undefined || value === "";
  }

  /** Server values are shown verbatim; unavailable values get a neutral placeholder. */
  function setText(node, value) {
    var missing = isMissing(value);
    node.textContent = missing ? PLACEHOLDER : String(value);
    node.classList.toggle("placeholder", missing);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined) {
      node.textContent = text;
    }
    return node;
  }

  function badge(label, modifier, small) {
    return el("span", "badge" + (modifier ? " " + modifier : "") + (small ? " badge--small" : ""), label);
  }

  function statusBadge(status) {
    if (isMissing(status)) {
      return el("span", "placeholder", PLACEHOLDER);
    }
    return badge(String(status), STATUS_BADGES[status] || "");
  }

  function modeBadge(isReplay) {
    return isReplay === true
      ? badge("Replay (non-live fixture)", "badge--replay")
      : badge("Live", "badge--live");
  }

  function cell(value, className) {
    var td = el("td", className || "");
    setText(td, value);
    return td;
  }

  /** Terminal means exactly the server-returned Approved / Rejected statuses. */
  function isTerminalDraft(draft) {
    return draft !== null && TERMINAL_STATUSES.indexOf(draft.status) !== -1;
  }

  /** UX guard only; the backend 409 stays authoritative for terminal drafts. */
  function canMutateDraft() {
    return state.draft !== null && !isTerminalDraft(state.draft) && state.approvedOrder === null;
  }

  /** One server-returned flag, shown verbatim. Resolved flags stay visible as history. */
  function renderDiscrepancy(flag) {
    var resolved = !isMissing(flag.resolution_state) && flag.resolution_state !== UNRESOLVED_STATE;
    var item = el("li", "flag " + (resolved ? "flag--resolved" : "flag--open"));

    var head = el("p", "flag__head");
    head.appendChild(el("span", "flag__type",
      isMissing(flag.discrepancy_type) ? "Discrepancy" : String(flag.discrepancy_type)));
    if (!isMissing(flag.severity)) {
      head.appendChild(el("span", "flag__tag", String(flag.severity)));
    }
    if (!isMissing(flag.resolution_state)) {
      head.appendChild(el("span", "flag__tag flag__tag--state", String(flag.resolution_state)));
    }
    item.appendChild(head);

    var values = el("dl", "flag__values");
    [["Expected", flag.expected_value], ["Requested", flag.requested_value]].forEach(function (row) {
      if (!isMissing(row[1])) {
        values.appendChild(el("dt", "", row[0]));
        values.appendChild(el("dd", "", String(row[1])));
      }
    });
    if (values.childNodes.length > 0) {
      item.appendChild(values);
    }
    if (!isMissing(flag.explanation)) {
      item.appendChild(el("p", "flag__explanation", String(flag.explanation)));
    }
    return item;
  }

  function candidateHint(candidate) {
    var parts = [];
    if (!isMissing(candidate.score)) {
      parts.push("Score: " + candidate.score);
    }
    if (!isMissing(candidate.rationale)) {
      parts.push(String(candidate.rationale));
    }
    return parts.join(" · ");
  }

  /** Operator controls for one Active line. Every control only triggers a server request. */
  function renderLineActions(line) {
    var actions = el("div", "line-actions");
    var label = "line " + (isMissing(line.line_number) ? "" : line.line_number);

    var candidates = (Array.isArray(line.candidate_skus) ? line.candidate_skus : []).filter(function (candidate) {
      return candidate && typeof candidate.sku === "string" && candidate.sku !== "";
    });
    if (candidates.length > 0) {
      var picker = el("div", "line-actions__candidates");
      var select = el("select", "line-actions__select");
      select.setAttribute("aria-label", "Candidate SKU for " + label);
      var prompt = el("option", "", "Select candidate…");
      prompt.value = "";
      select.appendChild(prompt);
      candidates.forEach(function (candidate) {
        var option = el("option", "", candidate.sku + (isMissing(candidate.name) ? "" : " — " + candidate.name));
        option.value = candidate.sku;
        select.appendChild(option);
      });
      var apply = el("button", "btn btn--small btn--primary", "Apply SKU");
      apply.type = "button";
      apply.setAttribute("data-requires-candidate", "");
      var hint = el("span", "line-actions__hint");
      // Changing the dropdown never mutates the order; only the explicit Apply action does.
      select.addEventListener("change", function () {
        var chosen = candidates.find(function (candidate) {
          return candidate.sku === select.value;
        });
        hint.textContent = chosen ? candidateHint(chosen) : "";
        renderControls();
      });
      apply.addEventListener("click", function () {
        if (select.value !== "") {
          selectLineSku(line, select.value);
        }
      });
      picker.appendChild(select);
      picker.appendChild(apply);
      actions.appendChild(picker);
      actions.appendChild(hint);
    }

    var buttons = el("div", "line-actions__buttons");
    var search = el("button", "btn btn--small btn--outline", "Search catalog…");
    search.type = "button";
    search.setAttribute("aria-label", "Search catalog for " + label);
    search.addEventListener("click", function () {
      openCatalogSearch(line);
    });
    var remove = el("button", "btn btn--small btn--danger", "Remove line");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + label);
    remove.addEventListener("click", function () {
      removeLine(line);
    });
    buttons.appendChild(search);
    buttons.appendChild(remove);
    actions.appendChild(buttons);
    return actions;
  }

  function renderLine(line) {
    var tr = el("tr", line.status === "Removed" ? "is-removed" : "");
    tr.appendChild(cell(line.line_number, "num"));
    tr.appendChild(cell(line.customer_description));
    tr.appendChild(cell(line.extracted_quantity, "num"));

    var skuCell = el("td");
    if (isMissing(line.matched_sku)) {
      setText(skuCell, null);
    } else {
      skuCell.appendChild(el("span", "sku", String(line.matched_sku)));
      if (!isMissing(line.sku_name)) {
        skuCell.appendChild(el("span", "sub", String(line.sku_name)));
      }
    }
    tr.appendChild(skuCell);

    tr.appendChild(cell(line.extracted_unit_price, "num"));
    tr.appendChild(cell(line.contract_price, "num"));
    tr.appendChild(cell(line.calculated_line_total, "num"));

    var resolutionCell = el("td", "resolution");
    var discrepancies = (Array.isArray(line.discrepancies) ? line.discrepancies : []).filter(Boolean);
    var removed = line.status === "Removed";
    var actionable = line.status === ACTIVE_LINE_STATUS && canMutateDraft();
    if (isMissing(line.sku_confidence) && isMissing(line.sku_resolution_source) &&
        discrepancies.length === 0 && !removed && !actionable) {
      setText(resolutionCell, null);
    } else {
      if (removed) {
        resolutionCell.appendChild(badge(String(line.status), "badge--rejected", true));
      }
      if (!isMissing(line.sku_confidence)) {
        resolutionCell.appendChild(badge(String(line.sku_confidence), "", true));
      }
      if (!isMissing(line.sku_resolution_source)) {
        resolutionCell.appendChild(el("span", "sub", String(line.sku_resolution_source)));
      }
    }
    if (discrepancies.length > 0) {
      var flags = el("ul", "flags");
      discrepancies.forEach(function (flag) {
        flags.appendChild(renderDiscrepancy(flag));
      });
      resolutionCell.appendChild(flags);
    }
    if (actionable) {
      resolutionCell.appendChild(renderLineActions(line));
    }
    tr.appendChild(resolutionCell);
    return tr;
  }

  function renderReplayBanner() {
    // Mandatory and unclosable whenever the rendered draft is replay data.
    dom.replayBanner.hidden = !(state.draft !== null && state.draft.is_replay_mode === true);
  }

  function renderDraft() {
    var draft = state.draft;
    renderReplayBanner();
    dom.draftEmpty.hidden = draft !== null;
    dom.draftSection.hidden = draft === null;
    if (draft === null) {
      return;
    }

    setText(dom.draftId, draft.draft_id);
    setText(dom.summaryCustomer, draft.customer_name_extracted);
    dom.summaryCustomerId.textContent = isMissing(draft.customer_id) ? "" : String(draft.customer_id);
    setText(dom.summaryPo, draft.po_number_extracted);
    dom.summaryStatus.replaceChildren(statusBadge(draft.status));
    dom.summaryMode.replaceChildren(modeBadge(draft.is_replay_mode));
    setText(dom.summarySubtotal, draft.calculated_subtotal);

    var lines = Array.isArray(draft.line_items) ? draft.line_items : [];
    dom.lineCount.textContent = lines.length + (lines.length === 1 ? " line" : " lines");
    if (lines.length === 0) {
      var emptyRow = el("tr", "lines__empty");
      var emptyCell = el("td", "", "The server returned no line items for this draft.");
      emptyCell.colSpan = 8;
      emptyRow.appendChild(emptyCell);
      dom.lineRows.replaceChildren(emptyRow);
    } else {
      dom.lineRows.replaceChildren.apply(dom.lineRows, lines.map(renderLine));
    }
  }

  function renderVerifiedOrder() {
    var order = state.approvedOrder;
    dom.resultSection.hidden = order === null;
    if (order === null) {
      return;
    }
    dom.resultMode.replaceChildren(modeBadge(order.is_replay_mode));
    setText(dom.resultOrderNumber, order.order_number);
    setText(dom.resultGrandTotal, order.grand_total);
    setText(dom.resultPo, order.po_number);
    setText(dom.resultCustomerId, order.customer_id);
    setText(dom.resultApprovedBy, order.approved_by);
    setText(dom.resultApprovedAt, order.approved_at);
    setText(dom.resultLines, order.line_items_count);
    setText(dom.resultOrderId, order.order_id);
  }

  function renderError() {
    var error = state.error;
    dom.diagnostics.hidden = error === null;
    if (error === null) {
      return;
    }
    dom.diagnosticContext.textContent = error.context;
    dom.diagnosticStatus.textContent = error.status === null ? "" : "HTTP " + error.status;
    dom.diagnosticError.textContent = error.error || "";
    dom.diagnosticMessage.textContent = error.message || "";
  }

  /** Enables/disables every action from current state; the single duplicate-submission guard. */
  function renderControls() {
    var busy = state.loading !== null;
    dom.activity.hidden = !busy;
    dom.activity.textContent = busy ? state.loading : "";

    dom.liveFile.disabled = busy;
    dom.dropzone.classList.toggle("is-disabled", busy);
    dom.liveSubmit.disabled = busy || state.liveFile === null;
    dom.liveFileName.hidden = state.liveFile === null;
    dom.liveFileName.textContent = state.liveFile === null ? "" : state.liveFile.name;

    dom.fixtureSelect.disabled = busy || state.fixtures.length === 0;
    dom.fixtureSubmit.disabled = busy || state.selectedFixtureId === "";

    // Approval eligibility is the server-returned draft.status, never recomputed here.
    var draft = state.draft;
    var ready = draft !== null && draft.status === READY_STATUS && state.approvedOrder === null;
    dom.approveSubmit.disabled = busy || !ready;
    dom.operatorId.disabled = busy || !ready;

    // P2 mutation controls: availability comes from server-returned state, busy blocks duplicates.
    Array.prototype.forEach.call(dom.lineRows.querySelectorAll(".line-actions select, .line-actions button"), function (control) {
      var needsCandidate = control.hasAttribute("data-requires-candidate");
      control.disabled = busy ||
        (needsCandidate && control.parentNode.querySelector("select").value === "");
    });
    rejectButton.hidden = !canMutateDraft();
    rejectButton.disabled = busy;
    if (rejectDialog !== null) {
      [rejectDialog.operatorId, rejectDialog.reason, rejectDialog.submit, rejectDialog.cancel].forEach(function (control) {
        control.disabled = busy;
      });
    }
    if (!dom.approvalHint.classList.contains("is-warning")) {
      if (draft === null) {
        dom.approvalHint.textContent = "";
      } else if (state.approvedOrder !== null) {
        dom.approvalHint.textContent = "This draft has been approved. See the verified order below.";
      } else if (ready) {
        dom.approvalHint.textContent = "Enter your operator ID to approve this draft.";
      } else {
        dom.approvalHint.textContent = "Approval is unavailable: draft status is “" +
          (isMissing(draft.status) ? PLACEHOLDER : draft.status) + "”, not “" + READY_STATUS + "”.";
      }
    }
  }

  function setApprovalWarning(text) {
    dom.approvalHint.classList.toggle("is-warning", text !== null);
    if (text !== null) {
      dom.approvalHint.textContent = text;
    }
  }

  function showError(error) {
    state.error = error instanceof ApiError
      ? error
      : new ApiError("Unexpected error", null, "ClientError", "The workspace hit an unexpected problem. Please retry the action.");
    renderError();
    dom.diagnostics.scrollIntoView({ block: "nearest" });
  }

  function clearError() {
    state.error = null;
    renderError();
  }

  function setLoading(label) {
    state.loading = label;
    renderControls();
  }

  /** Runs one operator-initiated request; ignores re-entry while another is pending. */
  async function runAction(label, action) {
    if (state.loading !== null) {
      return;
    }
    clearError();
    setApprovalWarning(null);
    setLoading(label);
    try {
      await action();
    } catch (error) {
      showError(error);
    } finally {
      setLoading(null);
    }
  }

  function showNewDraft(draft) {
    state.draft = draft;
    state.approvedOrder = null;
    dom.operatorId.value = "";
    renderDraft();
    renderVerifiedOrder();
  }

  // ---------- Live intake ----------

  function hasAllowedExtension(file) {
    var name = file.name.toLowerCase();
    return LIVE_EXTENSIONS.some(function (extension) {
      return name.endsWith(extension);
    });
  }

  function stageLiveFile(file) {
    if (state.loading !== null) {
      return;
    }
    if (!file) {
      state.liveFile = null;
    } else if (!hasAllowedExtension(file)) {
      state.liveFile = null;
      dom.liveFile.value = "";
      showError(new ApiError("Live intake", null, "UnsupportedFileType",
        "“" + file.name + "” was not sent. Live intake accepts only .txt or .pdf purchase orders."));
    } else {
      clearError();
      state.liveFile = file;
    }
    renderControls();
  }

  function ingestLiveFile() {
    var file = state.liveFile;
    if (file === null) {
      return;
    }
    return runAction("Ingesting live document…", async function () {
      var form = new FormData();
      form.append("file", file, file.name);
      // One live request. A failure is reported as a live failure: no retry, no replay fallback.
      var draft = await requestJson("Live intake failed", "/orders/ingest", { method: "POST", body: form });
      state.liveFile = null;
      dom.liveFile.value = "";
      showNewDraft(draft);
    });
  }

  // ---------- Fixture discovery / replay intake ----------

  function renderFixtureOptions() {
    var options = [];
    if (state.fixtures.length === 0) {
      var none = el("option", "", "No fixtures available");
      none.value = "";
      options.push(none);
    } else {
      var prompt = el("option", "", "Select a fixture…");
      prompt.value = "";
      options.push(prompt);
      state.fixtures.forEach(function (fixture) {
        var option = el("option", "", isMissing(fixture.name) ? fixture.fixture_id : fixture.name);
        option.value = fixture.fixture_id;
        options.push(option);
      });
    }
    dom.fixtureSelect.replaceChildren.apply(dom.fixtureSelect, options);
    dom.fixtureSelect.value = state.selectedFixtureId;
    renderFixtureDescription();
  }

  function selectedFixture() {
    return state.fixtures.find(function (fixture) {
      return fixture.fixture_id === state.selectedFixtureId;
    }) || null;
  }

  function renderFixtureDescription() {
    var fixture = selectedFixture();
    if (fixture === null) {
      dom.fixtureDescription.textContent = "";
      return;
    }
    var parts = [];
    if (!isMissing(fixture.description)) {
      parts.push(String(fixture.description));
    }
    if (!isMissing(fixture.document_filename)) {
      parts.push("Source: " + fixture.document_filename);
    }
    dom.fixtureDescription.textContent = parts.join(" · ");
  }

  function loadFixtures() {
    return runAction("Loading fixtures…", async function () {
      try {
        var fixtures = await requestJson("Fixture discovery failed", "/fixtures");
        state.fixtures = (Array.isArray(fixtures) ? fixtures : []).filter(function (fixture) {
          return fixture && typeof fixture.fixture_id === "string" && fixture.fixture_id !== "";
        });
      } finally {
        state.selectedFixtureId = "";
        renderFixtureOptions();
      }
    });
  }

  function ingestFixture() {
    // Only IDs supplied by the backend registry are ever sent.
    var fixture = selectedFixture();
    if (fixture === null) {
      return;
    }
    return runAction("Ingesting replay fixture…", async function () {
      var draft = await requestJson(
        "Replay intake failed",
        "/fixtures/" + encodeURIComponent(fixture.fixture_id) + "/ingest",
        { method: "POST" }
      );
      showNewDraft(draft);
    });
  }

  // ---------- Approval ----------

  function approveCurrentDraft() {
    var draft = state.draft;
    if (draft === null || draft.status !== READY_STATUS || state.approvedOrder !== null) {
      return;
    }
    var operatorId = dom.operatorId.value.trim();
    if (operatorId === "") {
      setApprovalWarning("Operator ID is required before approval can be sent.");
      dom.operatorId.focus();
      return;
    }
    return runAction("Approving order…", async function () {
      // Nothing is shown as approved until the backend confirms it.
      state.approvedOrder = await requestJson(
        "Approval failed",
        "/drafts/" + encodeURIComponent(draft.draft_id) + "/approve",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ operator_id: operatorId })
        }
      );
      renderVerifiedOrder();
      renderControls();
      dom.resultSection.scrollIntoView({ block: "nearest" });
      // Re-read the draft so the displayed status is the server's post-approval state.
      state.draft = await requestJson("Draft refresh failed", "/drafts/" + encodeURIComponent(draft.draft_id));
      renderDraft();
    });
  }

  // ---------- P2 operator mutations ----------

  function linePath(draft, line) {
    return "/drafts/" + encodeURIComponent(draft.draft_id) + "/lines/" + encodeURIComponent(line.line_id);
  }

  /** The one authoritative flow: HTTP response -> state.draft -> rerender. No optimistic update. */
  function mutateDraft(label, context, path, options) {
    return runAction(label, async function () {
      state.draft = await requestJson(context, path, options);
      renderDraft();
      renderControls();
    });
  }

  function selectLineSku(line, sku) {
    var draft = state.draft;
    if (!canMutateDraft()) {
      return;
    }
    return mutateDraft("Applying SKU…", "SKU selection failed", linePath(draft, line), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "SelectSKU", matched_sku: sku })
    });
  }

  function removeLine(line) {
    var draft = state.draft;
    if (!canMutateDraft() || state.loading !== null) {
      return;
    }
    var confirmed = window.confirm(
      "Remove line " + (isMissing(line.line_number) ? "" : line.line_number + " ") + "from this draft?\n\n" +
      "The server will preserve discrepancy history and recalculate the draft."
    );
    if (!confirmed) {
      return;
    }
    return mutateDraft("Removing line…", "Line removal failed", linePath(draft, line), { method: "DELETE" });
  }

  // ---------- Catalog search (accepted T033 component) ----------

  var catalogModalPromise = null;

  function catalogModalApi() {
    var api = window.OrderShieldCatalogModal;
    return api && typeof api.open === "function" && typeof api.close === "function" ? api : null;
  }

  /** Single-flight loader for the local T033 script; no remote source, no fallback UI. */
  function loadCatalogModal() {
    var loaded = catalogModalApi();
    if (loaded !== null) {
      return Promise.resolve(loaded);
    }
    if (catalogModalPromise === null) {
      catalogModalPromise = new Promise(function (resolve, reject) {
        var script = document.createElement("script");
        function fail(message) {
          // Drop the failed element so a later operator click cannot leave duplicates behind.
          script.remove();
          catalogModalPromise = null;
          reject(new ApiError("Catalog search unavailable", null, "CatalogModalLoadError", message));
        }
        script.src = CATALOG_MODAL_SRC;
        script.addEventListener("load", function () {
          var api = catalogModalApi();
          if (api === null) {
            fail("The catalog search component loaded but did not provide open/close.");
          } else {
            resolve(api);
          }
        });
        script.addEventListener("error", function () {
          fail("The catalog search component (" + CATALOG_MODAL_SRC + ") could not be loaded.");
        });
        document.head.appendChild(script);
      });
    }
    return catalogModalPromise;
  }

  function openCatalogSearch(line) {
    var draft = state.draft;
    if (!canMutateDraft()) {
      return;
    }
    return runAction("Opening catalog search…", async function () {
      var modal = await loadCatalogModal();
      modal.open({
        initialQuery: isMissing(line.customer_description) ? "" : String(line.customer_description),
        selectedSku: isMissing(line.matched_sku) ? "" : String(line.matched_sku),
        onSelect: function (product) {
          // The modal's choice is not persisted state: only the server's PATCH response is.
          if (state.loading !== null || state.draft !== draft || !product || isMissing(product.sku)) {
            return;
          }
          Promise.resolve(selectLineSku(line, String(product.sku))).then(function () {
            // Closed on success, and on failure too so the backend diagnostic is not hidden behind it.
            modal.close();
          });
        }
      });
    });
  }

  // ---------- Draft rejection ----------

  // index.html is frozen for T034, so the Reject action and its dialog are mounted from here.
  var rejectButton = el("button", "btn btn--danger btn--reject", "Reject Draft");
  rejectButton.type = "button";
  rejectButton.hidden = true;
  dom.approveForm.appendChild(rejectButton);

  var rejectDialog = null;   // DOM references once mounted
  var rejectReturnFocus = null;

  function rejectField(labelText, control, id) {
    var wrapper = el("label", "field");
    wrapper.htmlFor = id;
    control.id = id;
    wrapper.appendChild(el("span", "field__label", labelText));
    wrapper.appendChild(control);
    return wrapper;
  }

  function mountRejectDialog() {
    if (rejectDialog !== null) {
      return;
    }
    var backdrop = el("div", "reject-backdrop");
    backdrop.hidden = true;
    backdrop.addEventListener("click", closeRejectDialog);

    var modal = el("form", "reject-modal");
    modal.hidden = true;
    modal.noValidate = true;
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "reject-title");

    var title = el("h2", "reject-modal__title", "Reject draft");
    title.id = "reject-title";
    var intro = el("p", "reject-modal__intro",
      "Rejection is final for this draft. The server records the operator and reason.");
    var operatorId = el("input");
    operatorId.type = "text";
    operatorId.autocomplete = "off";
    operatorId.maxLength = 120;
    var reason = el("textarea", "reject-modal__reason");
    reason.rows = 4;
    var warning = el("p", "reject-modal__warning");
    warning.setAttribute("role", "alert");
    warning.hidden = true;

    var footer = el("div", "reject-modal__actions");
    var cancel = el("button", "btn btn--outline", "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", closeRejectDialog);
    var submit = el("button", "btn btn--danger", "Reject Draft");
    submit.type = "submit";
    footer.appendChild(cancel);
    footer.appendChild(submit);

    modal.appendChild(title);
    modal.appendChild(intro);
    modal.appendChild(rejectField("Operator ID", operatorId, "reject-operator-id"));
    modal.appendChild(rejectField("Reason", reason, "reject-reason"));
    modal.appendChild(warning);
    modal.appendChild(footer);
    modal.addEventListener("submit", function (event) {
      event.preventDefault();
      rejectCurrentDraft();
    });
    modal.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRejectDialog();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      // Keep focus inside the dialog so the page behind cannot be operated meanwhile.
      var focusable = [operatorId, reason, cancel, submit].filter(function (control) {
        return !control.disabled;
      });
      if (focusable.length === 0) {
        return;
      }
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    document.body.appendChild(backdrop);
    document.body.appendChild(modal);
    rejectDialog = {
      backdrop: backdrop, modal: modal, operatorId: operatorId, reason: reason,
      warning: warning, cancel: cancel, submit: submit,
      draft: null   // the draft this dialog was opened for
    };
  }

  function setRejectWarning(text) {
    rejectDialog.warning.hidden = text === null;
    rejectDialog.warning.textContent = text === null ? "" : text;
  }

  /** Keeps the dialog below a visible replay banner; the banner is only measured, never changed. */
  function positionRejectDialog() {
    var top = 12;
    if (!dom.replayBanner.hidden) {
      var rect = dom.replayBanner.getBoundingClientRect();
      if (rect.bottom > 0) {
        top = Math.round(rect.bottom) + 12;
      }
    }
    rejectDialog.modal.style.top = top + "px";
  }

  function openRejectDialog() {
    if (!canMutateDraft() || state.loading !== null) {
      return;
    }
    mountRejectDialog();
    rejectDialog.draft = state.draft;
    rejectReturnFocus = document.activeElement;
    // Prefill is a convenience only; the operator can edit it and nothing is invented.
    rejectDialog.operatorId.value = dom.operatorId.value.trim();
    rejectDialog.reason.value = "";
    setRejectWarning(null);
    rejectDialog.backdrop.hidden = false;
    rejectDialog.modal.hidden = false;
    positionRejectDialog();
    window.addEventListener("scroll", positionRejectDialog, true);
    window.addEventListener("resize", positionRejectDialog);
    (rejectDialog.operatorId.value === "" ? rejectDialog.operatorId : rejectDialog.reason).focus();
  }

  function closeRejectDialog() {
    // A pending rejection cannot be dismissed out from under its response.
    if (rejectDialog === null || rejectDialog.modal.hidden || state.loading !== null) {
      return;
    }
    rejectDialog.backdrop.hidden = true;
    rejectDialog.modal.hidden = true;
    window.removeEventListener("scroll", positionRejectDialog, true);
    window.removeEventListener("resize", positionRejectDialog);
    var target = rejectReturnFocus;
    rejectReturnFocus = null;
    if (target && typeof target.focus === "function" && document.contains(target) && !target.hidden) {
      target.focus();
    }
  }

  function rejectCurrentDraft() {
    var draft = state.draft;
    if (!canMutateDraft() || state.loading !== null || rejectDialog.draft !== draft) {
      return;
    }
    // Presence checks only; rejection semantics are validated by the backend.
    var operatorId = rejectDialog.operatorId.value.trim();
    var reason = rejectDialog.reason.value.trim();
    if (operatorId === "") {
      setRejectWarning("Operator ID is required before the rejection can be sent.");
      rejectDialog.operatorId.focus();
      return;
    }
    if (reason === "") {
      setRejectWarning("A rejection reason is required before the rejection can be sent.");
      rejectDialog.reason.focus();
      return;
    }
    setRejectWarning(null);
    return mutateDraft("Rejecting draft…", "Rejection failed",
      "/drafts/" + encodeURIComponent(draft.draft_id) + "/reject",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operator_id: operatorId, reason: reason })
      }
    ).then(function () {
      if (state.error === null) {
        closeRejectDialog();
      } else {
        // The dialog covers the page diagnostics, so the same backend diagnostic is repeated here.
        setRejectWarning([
          state.error.context,
          state.error.status === null ? "" : "HTTP " + state.error.status,
          state.error.error,
          state.error.message
        ].filter(Boolean).join(" · "));
      }
    });
  }

  // ---------- Event listeners ----------

  rejectButton.addEventListener("click", openRejectDialog);

  dom.liveFile.addEventListener("change", function () {
    stageLiveFile(dom.liveFile.files.length > 0 ? dom.liveFile.files[0] : null);
  });

  ["dragenter", "dragover"].forEach(function (type) {
    dom.dropzone.addEventListener(type, function (event) {
      event.preventDefault();
      if (state.loading === null) {
        dom.dropzone.classList.add("is-dragover");
      }
    });
  });

  ["dragleave", "drop"].forEach(function (type) {
    dom.dropzone.addEventListener(type, function (event) {
      event.preventDefault();
      dom.dropzone.classList.remove("is-dragover");
    });
  });

  dom.dropzone.addEventListener("drop", function (event) {
    var files = event.dataTransfer ? event.dataTransfer.files : null;
    if (files && files.length > 0) {
      dom.liveFile.value = "";
      stageLiveFile(files[0]);
    }
  });

  // A file dropped outside the dropzone must not navigate the browser away from the workspace.
  ["dragover", "drop"].forEach(function (type) {
    window.addEventListener(type, function (event) {
      event.preventDefault();
    });
  });

  dom.liveForm.addEventListener("submit", function (event) {
    event.preventDefault();
    ingestLiveFile();
  });

  dom.fixtureSelect.addEventListener("change", function () {
    state.selectedFixtureId = dom.fixtureSelect.value;
    renderFixtureDescription();
    renderControls();
  });

  dom.fixtureForm.addEventListener("submit", function (event) {
    event.preventDefault();
    ingestFixture();
  });

  dom.approveForm.addEventListener("submit", function (event) {
    event.preventDefault();
    approveCurrentDraft();
  });

  dom.operatorId.addEventListener("input", function () {
    if (dom.approvalHint.classList.contains("is-warning")) {
      setApprovalWarning(null);
      renderControls();
    }
  });

  dom.diagnosticDismiss.addEventListener("click", clearError);

  // ---------- Initialization ----------

  renderDraft();
  renderVerifiedOrder();
  renderError();
  renderControls();
  loadFixtures();
})();
