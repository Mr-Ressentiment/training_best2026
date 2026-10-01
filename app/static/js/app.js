/* OrderShield P1 workspace logic.
 *
 * The backend is authoritative for status, pricing, totals, readiness, SKU
 * resolution and approval outcome. This file only transports requests and
 * renders response bodies; it never recomputes business rules.
 *
 * LIVE   -> POST /api/v1/orders/ingest
 * REPLAY -> POST /api/v1/fixtures/{fixture_id}/ingest
 * The two paths are never substituted for each other and nothing is retried.
 */
(function () {
  "use strict";

  var API = "/api/v1";
  var PLACEHOLDER = "—";
  var READY_STATUS = "Ready for Approval";
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

    var resolutionCell = el("td");
    if (isMissing(line.sku_confidence) && isMissing(line.sku_resolution_source)) {
      setText(resolutionCell, null);
    } else {
      if (!isMissing(line.sku_confidence)) {
        resolutionCell.appendChild(badge(String(line.sku_confidence), "", true));
      }
      if (!isMissing(line.sku_resolution_source)) {
        resolutionCell.appendChild(el("span", "sub", String(line.sku_resolution_source)));
      }
    }
    // Read-only display of server-returned flags; resolution controls are P2.
    var discrepancies = Array.isArray(line.discrepancies) ? line.discrepancies : [];
    if (discrepancies.length > 0) {
      var types = discrepancies.map(function (flag) {
        return flag && !isMissing(flag.discrepancy_type) ? String(flag.discrepancy_type) : "Discrepancy";
      });
      resolutionCell.appendChild(el("span", "note", "⚑ " + types.join(", ")));
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

  // ---------- Event listeners ----------

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
