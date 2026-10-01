/* OrderShield catalog search modal (T033).
 *
 * Catalog discovery and operator SKU choice only. The backend owns search
 * semantics: this component sends GET /api/v1/catalog and renders the
 * response as returned, with no local matching, ranking or price logic.
 * Choosing a product hands it to the caller's onSelect; nothing is mutated
 * here and nothing is claimed as resolved. Every catalog value is untrusted
 * display text and is written through textContent only.
 *
 * Public API (plain script, no modules or build step):
 *   OrderShieldCatalogModal.open({ onSelect, initialQuery, initialCategory, selectedSku })
 *   OrderShieldCatalogModal.close()
 *   OrderShieldCatalogModal.isOpen()
 *   OrderShieldCatalogModal.destroy()
 */
(function () {
  "use strict";

  if (window.OrderShieldCatalogModal) {
    return;
  }

  var CATALOG_URL = "/api/v1/catalog";
  var PLACEHOLDER = "—";
  var STYLE_ID = "os-cat-modal-styles";
  var TITLE_ID = "os-cat-modal-title";
  var QUERY_ID = "os-cat-query";
  var CATEGORY_ID = "os-cat-category";
  var EMPTY_MESSAGE = "No catalog products match this search.";
  var REPLAY_BANNER_SELECTOR = ".banner-nonlive";
  var EDGE_GAP = 12;

  // Scoped to .os-cat-*; shared styles.css is intentionally untouched.
  // z-index stays below the replay banner (10) so the modal can never cover it.
  var CSS = [
    ".os-cat-backdrop{position:fixed;inset:0;z-index:8;background:rgba(23,33,43,.45);}",
    ".os-cat-modal{position:fixed;top:12px;bottom:12px;left:50%;transform:translateX(-50%);z-index:9;",
    "display:flex;flex-direction:column;width:780px;max-width:calc(100vw - 24px);box-sizing:border-box;",
    "background:#fff;color:#17212b;border:1px solid #b9c3cd;border-top:4px solid #0b5f8a;border-radius:8px;",
    "box-shadow:0 12px 32px rgba(23,33,43,.25);overflow:hidden;overflow-wrap:anywhere;",
    "font-family:system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;",
    "font-size:15px;line-height:1.45;}",
    ".os-cat-modal *{box-sizing:border-box;}",
    ".os-cat-backdrop[hidden],.os-cat-modal[hidden],.os-cat-modal [hidden]{display:none;}",
    ".os-cat-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;",
    "padding:12px 18px;border-bottom:1px solid #d9dfe5;}",
    ".os-cat-eyebrow{margin:0 0 2px;font-size:.72rem;font-weight:700;text-transform:uppercase;",
    "letter-spacing:.06em;color:#6d7b89;}",
    ".os-cat-title{margin:0;font-size:1.1rem;font-weight:700;}",
    ".os-cat-btn{flex:none;font:inherit;font-weight:700;padding:8px 14px;border-radius:6px;",
    "border:1px solid transparent;cursor:pointer;}",
    ".os-cat-btn:disabled{cursor:not-allowed;background:#e9edf1;border-color:#d9dfe5;color:#6d7b89;}",
    ".os-cat-btn--quiet{background:#fff;border-color:#b9c3cd;color:#17212b;font-weight:600;padding:5px 12px;}",
    ".os-cat-btn--quiet:hover{background:#eef1f4;}",
    ".os-cat-btn--primary{background:#0b5f8a;color:#fff;}",
    ".os-cat-btn--primary:hover:not(:disabled){background:#084a6c;}",
    ".os-cat-btn--select{background:#fff;border-color:#0b5f8a;color:#0b5f8a;}",
    ".os-cat-btn--select:hover{background:#e6f1f7;}",
    ".os-cat-modal :focus-visible{outline:3px solid #7ab8dc;outline-offset:1px;}",
    ".os-cat-form{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px 12px;padding:12px 18px;",
    "border-bottom:1px solid #d9dfe5;background:#fafbfc;}",
    ".os-cat-field{display:flex;flex-direction:column;gap:4px;min-width:0;}",
    ".os-cat-field--query{flex:1 1 240px;}",
    ".os-cat-field--category{flex:0 1 200px;}",
    ".os-cat-label{font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:#4b5a69;}",
    ".os-cat-input{font:inherit;color:inherit;width:100%;min-width:0;padding:8px 10px;border:1px solid #b9c3cd;",
    "border-radius:6px;background:#fff;}",
    ".os-cat-body{flex:1;min-height:0;overflow-y:auto;padding:12px 18px 16px;}",
    ".os-cat-status{margin:0 0 10px;color:#4b5a69;font-size:.88rem;}",
    ".os-cat-note{margin:0 0 10px;padding:8px 12px;border:1px solid #9cc6dc;border-left:4px solid #0b5f8a;",
    "border-radius:6px;background:#e6f1f7;font-size:.9rem;}",
    ".os-cat-diagnostic{margin:0 0 10px;padding:10px 12px;border:1px solid #eda4a4;border-left:6px solid #9b1c1c;",
    "border-radius:6px;background:#fde8e8;color:#9b1c1c;}",
    ".os-cat-diagnostic-title{margin:0;font-weight:700;}",
    ".os-cat-diagnostic-message{margin:4px 0 0;color:#5c1414;}",
    ".os-cat-empty{margin:0;padding:22px 12px;border:1px dashed #b9c3cd;border-radius:6px;text-align:center;",
    "color:#4b5a69;}",
    ".os-cat-results{margin:0;padding:0;list-style:none;display:grid;gap:10px;}",
    ".os-cat-item{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:12px 14px;",
    "border:1px solid #d9dfe5;border-radius:6px;background:#fff;}",
    ".os-cat-item--chosen{border-color:#0b5f8a;background:#f3f9fc;}",
    ".os-cat-item-main{flex:1;min-width:0;}",
    ".os-cat-sku{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;",
    "font-size:.9rem;font-weight:700;}",
    ".os-cat-name{margin:0 0 8px;font-weight:600;}",
    ".os-cat-tag{display:inline-block;margin-left:8px;padding:1px 8px;border-radius:999px;border:1px solid #9cc6dc;",
    "background:#e6f1f7;color:#0b5f8a;font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;",
    "font-size:.72rem;font-weight:700;}",
    ".os-cat-details{margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:6px 14px;}",
    ".os-cat-details dt{margin:0;font-size:.72rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;",
    "color:#6d7b89;}",
    ".os-cat-details dd{margin:0;font-weight:600;font-variant-numeric:tabular-nums;}",
    ".os-cat-placeholder{color:#6d7b89;font-weight:400;}",
    "@media (max-width:560px){.os-cat-item{flex-direction:column;}.os-cat-item .os-cat-btn{align-self:stretch;}",
    ".os-cat-field--category{flex:1 1 100%;}.os-cat-form .os-cat-btn{flex:1 1 100%;}",
    ".os-cat-details{grid-template-columns:repeat(2,minmax(0,1fr));}}"
  ].join("");

  var nodes = null;        // DOM references once mounted
  var open = false;
  var previousFocus = null;
  var session = null;      // per-open state: {onSelect, selectedSku, chosenSku, categories, products, pending}
  var generation = 0;      // bumped on every open/close/destroy; stale responses are ignored
  var controller = null;

  // ---------- Safe DOM helpers ----------

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

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  /** Scalars are shown verbatim as text; anything else is treated as unavailable. */
  function displayText(value) {
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
    return "";
  }

  function valueNode(tag, value) {
    var text = displayText(value);
    return text === "" ? el(tag, "os-cat-placeholder", PLACEHOLDER) : el(tag, "", text);
  }

  // ---------- Rendering ----------

  function renderCategories() {
    var current = nodes.category.value;
    var options = [el("option", "", "All categories")];
    options[0].value = "";
    session.categories.forEach(function (category) {
      var option = el("option", "", category);
      option.value = category;
      options.push(option);
    });
    nodes.category.replaceChildren.apply(nodes.category, options);
    nodes.category.value = session.categories.indexOf(current) === -1 ? "" : current;
  }

  /** Category names come only from backend responses (and the caller's initial value). */
  function rememberCategories(products) {
    products.forEach(function (product) {
      var category = displayText(product.category);
      if (category !== "" && session.categories.indexOf(category) === -1) {
        session.categories.push(category);
      }
    });
    session.categories.sort();
    renderCategories();
  }

  function renderProduct(product) {
    var sku = displayText(product.sku);
    var item = el("li", "os-cat-item" + (sku !== "" && sku === session.chosenSku ? " os-cat-item--chosen" : ""));
    var main = el("div", "os-cat-item-main");

    var skuLine = valueNode("p", product.sku);
    skuLine.classList.add("os-cat-sku");
    if (sku !== "" && sku === session.chosenSku) {
      skuLine.appendChild(el("span", "os-cat-tag", "Selected"));
    } else if (sku !== "" && sku === session.selectedSku) {
      skuLine.appendChild(el("span", "os-cat-tag", "Current SKU"));
    }
    main.appendChild(skuLine);

    var name = valueNode("p", product.name);
    name.classList.add("os-cat-name");
    main.appendChild(name);

    // base_price is the catalog reference price as returned; contract pricing stays server-side.
    var details = el("dl", "os-cat-details");
    [
      ["Category", product.category],
      ["Unit", product.unit_of_measure],
      ["Catalog base price", product.base_price],
      ["Minimum order quantity", product.min_order_quantity],
      ["Package increment", product.package_increment]
    ].forEach(function (row) {
      var group = el("div");
      group.appendChild(el("dt", "", row[0]));
      group.appendChild(valueNode("dd", row[1]));
      details.appendChild(group);
    });
    main.appendChild(details);
    item.appendChild(main);

    if (sku !== "") {
      var select = el("button", "os-cat-btn os-cat-btn--select", "Select SKU");
      select.type = "button";
      select.setAttribute("aria-label", "Select SKU " + sku);
      select.addEventListener("click", function () {
        selectProduct(product);
      });
      item.appendChild(select);
    }
    return item;
  }

  function renderResults() {
    var products = session.products;
    if (products === null) {
      nodes.results.replaceChildren();
      nodes.empty.hidden = true;
      return;
    }
    nodes.empty.hidden = products.length !== 0;
    nodes.results.replaceChildren.apply(nodes.results, products.map(renderProduct));
  }

  function renderNote() {
    // Neutral wording: the caller, not this modal, applies and confirms the choice.
    nodes.note.hidden = session.chosenSku === null;
    nodes.note.textContent = session.chosenSku === null
      ? ""
      : "You selected " + session.chosenSku + ". It is not applied to the order until the server confirms it.";
  }

  function showDiagnostic(title, message) {
    nodes.diagnosticTitle.textContent = title;
    nodes.diagnosticMessage.textContent = message;
    nodes.diagnostic.hidden = false;
  }

  function clearDiagnostic() {
    nodes.diagnostic.hidden = true;
    nodes.diagnosticTitle.textContent = "";
    nodes.diagnosticMessage.textContent = "";
  }

  function setPending(pending) {
    session.pending = pending;
    nodes.search.disabled = pending;
    nodes.search.textContent = pending ? "Searching…" : "Search";
    if (pending) {
      nodes.status.textContent = "Loading catalog…";
    }
  }

  function describeSearch(query, category, count) {
    var text = count + (count === 1 ? " product" : " products");
    if (query !== "") {
      text += " for “" + query + "”";
    }
    if (category !== "") {
      text += " in " + category;
    }
    return text;
  }

  // ---------- Catalog lookup ----------

  function failure(title, message) {
    return { title: title, message: message };
  }

  /** One GET, no retry. Resolves to the product array or rejects with {title, message}. */
  async function fetchCatalog(query, category, signal) {
    var params = new URLSearchParams();
    if (query !== "") {
      params.set("query", query);
    }
    if (category !== "") {
      params.set("category", category);
    }
    var search = params.toString();
    var response;
    try {
      response = await fetch(CATALOG_URL + (search === "" ? "" : "?" + search), {
        method: "GET",
        headers: { Accept: "application/json" },
        signal: signal
      });
    } catch (networkError) {
      throw failure("Catalog lookup failed", "Could not reach the OrderShield server. Check that it is running, then search again.");
    }
    var body;
    var parsed = true;
    try {
      body = await response.json();
    } catch (parseError) {
      parsed = false;
    }
    if (!response.ok) {
      var error = parsed && isObject(body) ? displayText(body.error) : "";
      var message = parsed && isObject(body) ? displayText(body.message) : "";
      throw failure(
        "Catalog lookup failed · HTTP " + response.status + (error === "" ? "" : " · " + error),
        message === "" ? "The server returned an error without a diagnostic message." : message
      );
    }
    if (!parsed) {
      throw failure("Catalog lookup failed · InvalidResponse", "The server response was not valid JSON.");
    }
    if (!Array.isArray(body)) {
      throw failure("Catalog lookup failed · InvalidResponse", "The server response was not a list of catalog products.");
    }
    return body;
  }

  async function runSearch() {
    if (!open || session.pending) {
      return;
    }
    var token = generation;
    var current = session;
    var query = nodes.query.value.trim();
    var category = nodes.category.value;
    clearDiagnostic();
    setPending(true);
    controller = typeof AbortController === "function" ? new AbortController() : null;
    var products = null;
    var problem = null;
    try {
      products = await fetchCatalog(query, category, controller ? controller.signal : undefined);
    } catch (error) {
      problem = isObject(error) && typeof error.title === "string"
        ? error
        : failure("Catalog lookup failed", "The catalog response could not be processed.");
    }
    // Closed, destroyed or reopened meanwhile: this response no longer owns the UI.
    if (token !== generation || nodes === null || session !== current) {
      return;
    }
    controller = null;
    setPending(false);
    if (problem !== null) {
      // Never keep earlier results on screen as if they answered this search.
      session.products = null;
      renderResults();
      nodes.status.textContent = "";
      showDiagnostic(problem.title, problem.message);
      return;
    }
    var usable = products.filter(isObject);
    session.products = usable;
    rememberCategories(usable);
    renderResults();
    nodes.body.scrollTop = 0;
    nodes.status.textContent = describeSearch(query, category, usable.length) +
      (usable.length === products.length ? "" : " · " + (products.length - usable.length) + " malformed entries not shown");
  }

  function selectProduct(product) {
    if (!open) {
      return;
    }
    var chosen = Object.assign({}, product);
    session.chosenSku = displayText(product.sku);
    renderResults();
    renderNote();
    if (typeof session.onSelect !== "function") {
      return;
    }
    try {
      session.onSelect(chosen);
    } catch (handlerError) {
      showDiagnostic("Selection could not be handed over", "The workspace did not accept the selected product. Nothing was changed.");
    }
  }

  // ---------- Mounting / lifecycle ----------

  function field(className, labelText, control, id) {
    var wrapper = el("div", "os-cat-field " + className);
    var label = el("label", "os-cat-label", labelText);
    label.htmlFor = id;
    control.id = id;
    wrapper.appendChild(label);
    wrapper.appendChild(control);
    return wrapper;
  }

  function mount() {
    if (nodes !== null) {
      return;
    }
    if (!document.getElementById(STYLE_ID)) {
      var style = el("style", "", CSS);
      style.id = STYLE_ID;
      document.head.appendChild(style);
    }

    var backdrop = el("div", "os-cat-backdrop");
    backdrop.hidden = true;
    backdrop.addEventListener("click", close);

    var modal = el("div", "os-cat-modal");
    modal.hidden = true;
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", TITLE_ID);

    var head = el("header", "os-cat-head");
    var heading = el("div");
    heading.appendChild(el("p", "os-cat-eyebrow", "Master catalog"));
    var title = el("h2", "os-cat-title", "Search catalog products");
    title.id = TITLE_ID;
    heading.appendChild(title);
    var closeButton = el("button", "os-cat-btn os-cat-btn--quiet", "Close");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Close catalog search");
    closeButton.addEventListener("click", close);
    head.appendChild(heading);
    head.appendChild(closeButton);

    var form = el("form", "os-cat-form");
    form.noValidate = true;
    var query = el("input", "os-cat-input");
    query.type = "search";
    query.autocomplete = "off";
    query.placeholder = "SKU or product name";
    var category = el("select", "os-cat-input");
    var search = el("button", "os-cat-btn os-cat-btn--primary", "Search");
    search.type = "submit";
    form.appendChild(field("os-cat-field--query", "Keyword", query, QUERY_ID));
    form.appendChild(field("os-cat-field--category", "Category", category, CATEGORY_ID));
    form.appendChild(search);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      runSearch();
    });

    var body = el("div", "os-cat-body");
    var diagnostic = el("div", "os-cat-diagnostic");
    diagnostic.setAttribute("role", "alert");
    diagnostic.hidden = true;
    var diagnosticTitle = el("p", "os-cat-diagnostic-title");
    var diagnosticMessage = el("p", "os-cat-diagnostic-message");
    diagnostic.appendChild(diagnosticTitle);
    diagnostic.appendChild(diagnosticMessage);
    var note = el("p", "os-cat-note");
    note.setAttribute("role", "status");
    note.hidden = true;
    var status = el("p", "os-cat-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    var empty = el("p", "os-cat-empty", EMPTY_MESSAGE);
    empty.hidden = true;
    var results = el("ul", "os-cat-results");
    body.appendChild(diagnostic);
    body.appendChild(note);
    body.appendChild(status);
    body.appendChild(empty);
    body.appendChild(results);

    modal.appendChild(head);
    modal.appendChild(form);
    modal.appendChild(body);
    document.body.appendChild(backdrop);
    document.body.appendChild(modal);
    nodes = {
      backdrop: backdrop, modal: modal, closeButton: closeButton, query: query, category: category,
      search: search, diagnostic: diagnostic, diagnosticTitle: diagnosticTitle,
      diagnosticMessage: diagnosticMessage, note: note, status: status, empty: empty, results: results,
      body: body
    };
  }

  /** Starts the modal below a visible replay banner; the banner itself is only measured, never changed. */
  function position() {
    if (nodes === null) {
      return;
    }
    var top = EDGE_GAP;
    var banner = document.querySelector(REPLAY_BANNER_SELECTOR);
    if (banner && !banner.hidden) {
      var rect = banner.getBoundingClientRect();
      if (rect.height > 0 && rect.bottom > 0 && rect.bottom < window.innerHeight / 2) {
        top = Math.round(rect.bottom) + EDGE_GAP;
      }
    }
    nodes.modal.style.top = top + "px";
  }

  function focusableElements() {
    return Array.prototype.filter.call(
      nodes.modal.querySelectorAll("button, input, select"),
      function (node) {
        return !node.disabled && node.offsetParent !== null;
      }
    );
  }

  function onKeydown(event) {
    if (!open) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    // Simple local focus loop: wrap at the ends, keep focus inside the modal.
    var focusable = focusableElements();
    if (focusable.length === 0) {
      return;
    }
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    var inside = nodes.modal.contains(document.activeElement);
    if (event.shiftKey && (!inside || document.activeElement === first)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (!inside || document.activeElement === last)) {
      event.preventDefault();
      first.focus();
    }
  }

  function abortPending() {
    generation += 1;
    if (controller !== null) {
      controller.abort();
      controller = null;
    }
  }

  function openModal(options) {
    var settings = isObject(options) ? options : {};
    mount();
    abortPending();
    if (!open) {
      previousFocus = document.activeElement;
      document.addEventListener("keydown", onKeydown, true);
      window.addEventListener("scroll", position, true);
      window.addEventListener("resize", position);
    }
    var initialCategory = displayText(settings.initialCategory);
    session = {
      onSelect: typeof settings.onSelect === "function" ? settings.onSelect : null,
      selectedSku: displayText(settings.selectedSku),
      chosenSku: null,
      categories: initialCategory === "" ? [] : [initialCategory],
      products: null,
      pending: false
    };
    clearDiagnostic();
    renderNote();
    renderResults();
    nodes.status.textContent = "";
    nodes.query.value = displayText(settings.initialQuery);
    renderCategories();
    nodes.category.value = initialCategory;
    nodes.backdrop.hidden = false;
    nodes.modal.hidden = false;
    open = true;
    position();
    setPending(false);
    nodes.query.focus();
    runSearch();
  }

  function close() {
    if (!open) {
      return;
    }
    open = false;
    abortPending();
    document.removeEventListener("keydown", onKeydown, true);
    window.removeEventListener("scroll", position, true);
    window.removeEventListener("resize", position);
    nodes.backdrop.hidden = true;
    nodes.modal.hidden = true;
    // Transient state is dropped; the next open() loads the catalog afresh.
    session = null;
    nodes.results.replaceChildren();
    nodes.query.value = "";
    clearDiagnostic();
    var target = previousFocus;
    previousFocus = null;
    if (target && typeof target.focus === "function" && document.contains(target)) {
      target.focus();
    }
  }

  function destroy() {
    close();
    abortPending();
    if (nodes !== null) {
      nodes.backdrop.remove();
      nodes.modal.remove();
      nodes = null;
    }
    var style = document.getElementById(STYLE_ID);
    if (style) {
      style.remove();
    }
  }

  window.OrderShieldCatalogModal = {
    open: openModal,
    close: close,
    isOpen: function () {
      return open;
    },
    destroy: destroy
  };
})();
