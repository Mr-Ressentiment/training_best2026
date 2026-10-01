/* OrderShield field provenance drawer (T037).
 *
 * Presentation-only component: it renders source-grounding data handed to it
 * by the caller and never fetches, infers, reconstructs or recalculates
 * anything. Every supplied value is untrusted display text and is written
 * through textContent only.
 *
 * Public API (plain script, no modules or build step):
 *   OrderShieldProvenanceDrawer.open({ label, value, provenance, matching })
 *   OrderShieldProvenanceDrawer.close()
 *   OrderShieldProvenanceDrawer.isOpen()
 *   OrderShieldProvenanceDrawer.destroy()
 */
(function () {
  "use strict";

  if (window.OrderShieldProvenanceDrawer) {
    return;
  }

  var PLACEHOLDER = "—";
  var STYLE_ID = "os-prov-drawer-styles";
  var TITLE_ID = "os-prov-drawer-title";
  var EVIDENCE_UNAVAILABLE = "Source evidence unavailable";
  var LOCATION_UNAVAILABLE = "Source location unavailable";
  var REPLAY_BANNER_SELECTOR = ".banner-nonlive";

  // Scoped to .os-prov-*; shared styles.css is intentionally untouched.
  // z-index stays below the replay banner (10) so the drawer can never cover it.
  var CSS = [
    ".os-prov-backdrop{position:fixed;inset:0;z-index:8;background:rgba(23,33,43,.45);}",
    ".os-prov-drawer{position:fixed;top:0;right:0;bottom:0;z-index:9;display:flex;flex-direction:column;",
    "width:440px;max-width:100vw;box-sizing:border-box;background:#fff;color:#17212b;",
    "border-left:1px solid #b9c3cd;box-shadow:-8px 0 24px rgba(23,33,43,.18);",
    "font-family:system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;",
    "font-size:15px;line-height:1.45;overflow-wrap:anywhere;}",
    ".os-prov-drawer *{box-sizing:border-box;}",
    ".os-prov-backdrop[hidden],.os-prov-drawer[hidden]{display:none;}",
    ".os-prov-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;",
    "padding:14px 18px;border-bottom:1px solid #d9dfe5;border-top:4px solid #0b5f8a;}",
    ".os-prov-eyebrow{margin:0 0 2px;font-size:.72rem;font-weight:700;text-transform:uppercase;",
    "letter-spacing:.06em;color:#6d7b89;}",
    ".os-prov-title{margin:0;font-size:1.1rem;font-weight:700;min-width:0;}",
    ".os-prov-close{flex:none;font:inherit;font-weight:600;padding:5px 12px;border-radius:6px;",
    "border:1px solid #b9c3cd;background:#fff;color:#17212b;cursor:pointer;}",
    ".os-prov-close:hover{background:#eef1f4;}",
    ".os-prov-close:focus-visible,.os-prov-body:focus-visible{outline:3px solid #7ab8dc;outline-offset:1px;}",
    ".os-prov-body{flex:1;min-height:0;overflow-y:auto;padding:4px 18px 20px;}",
    ".os-prov-section{padding:14px 0;border-bottom:1px solid #d9dfe5;}",
    ".os-prov-section:last-child{border-bottom:0;}",
    ".os-prov-section-title{margin:0 0 8px;font-size:.78rem;font-weight:700;text-transform:uppercase;",
    "letter-spacing:.05em;color:#4b5a69;}",
    ".os-prov-list{margin:0;display:grid;grid-template-columns:minmax(0,150px) minmax(0,1fr);gap:6px 12px;}",
    ".os-prov-list dt{margin:0;color:#6d7b89;font-size:.85rem;}",
    ".os-prov-list dd{margin:0;font-weight:600;min-width:0;}",
    ".os-prov-mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;font-size:.85rem;}",
    ".os-prov-snippet{margin:0 0 10px;padding:10px 12px;border:1px solid #9cc6dc;border-left:4px solid #0b5f8a;",
    "border-radius:6px;background:#e6f1f7;white-space:pre-wrap;",
    "font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;font-size:.88rem;}",
    ".os-prov-caption{margin:0 0 4px;color:#6d7b89;font-size:.8rem;}",
    ".os-prov-unavailable{margin:0 0 10px;padding:8px 12px;border:1px dashed #b9c3cd;border-radius:6px;",
    "background:#f3f5f7;color:#4b5a69;font-size:.9rem;}",
    ".os-prov-unavailable:last-child,.os-prov-snippet:last-child{margin-bottom:0;}",
    ".os-prov-placeholder{color:#6d7b89;font-weight:400;}",
    ".os-prov-rationale-label{margin-top:10px;}",
    ".os-prov-rationale{margin:0;white-space:pre-wrap;}",
    ".os-prov-candidates{margin:6px 0 0;padding:0;list-style:none;}",
    ".os-prov-candidates li{padding:8px 0;border-top:1px solid #e9edf1;}",
    ".os-prov-candidates li:first-child{border-top:0;}",
    ".os-prov-sub{display:block;color:#6d7b89;font-size:.82rem;font-weight:400;}",
    "@media (max-width:480px){.os-prov-drawer{width:100vw;}.os-prov-list{grid-template-columns:minmax(0,1fr);gap:0;}",
    ".os-prov-list dd{margin-bottom:8px;}}"
  ].join("");

  var nodes = null;          // {backdrop, drawer, title, closeButton, body} once mounted
  var open = false;
  var previousFocus = null;

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

  function isMissing(value) {
    return value === null || value === undefined || value === "";
  }

  /** Scalars are shown verbatim as text; anything else is treated as unavailable. */
  function displayText(value) {
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      return String(value);
    }
    return "";
  }

  function isOffset(value) {
    return typeof value === "number" && isFinite(value);
  }

  function valueNode(tag, value, className) {
    var text = displayText(value);
    if (text === "") {
      return el(tag, "os-prov-placeholder", PLACEHOLDER);
    }
    return el(tag, className || "", text);
  }

  function definitionList(rows) {
    var list = el("dl", "os-prov-list");
    rows.forEach(function (row) {
      list.appendChild(el("dt", "", row[0]));
      list.appendChild(valueNode("dd", row[1], row[2]));
    });
    return list;
  }

  function section(title) {
    var node = el("section", "os-prov-section");
    node.appendChild(el("h3", "os-prov-section-title", title));
    return node;
  }

  // ---------- Rendering ----------

  function renderField(payload, provenance) {
    var node = section("Field");
    node.appendChild(definitionList([
      ["Current value", payload.value],
      ["Field", provenance ? provenance.field_name : null, "os-prov-mono"]
    ]));
    return node;
  }

  /** Canonical backend offsets only; malformed shapes yield no rows, never invented coordinates. */
  function locationRows(location) {
    if (!isObject(location)) {
      return null;
    }
    if (location.type === "txt" && isOffset(location.line_number) && isOffset(location.char_offset)) {
      return [
        ["Source type", "TXT source"],
        ["Line", location.line_number],
        ["Character offset", location.char_offset]
      ];
    }
    if (location.type === "pdf" && isOffset(location.page_number) &&
        isOffset(location.char_start) && isOffset(location.char_end)) {
      return [
        ["Source type", "PDF source"],
        ["Page", location.page_number],
        ["Characters", location.char_start + "–" + location.char_end]
      ];
    }
    return null;
  }

  function renderEvidence(provenance) {
    var node = section("Source evidence");
    if (!provenance) {
      node.appendChild(el("p", "os-prov-unavailable", EVIDENCE_UNAVAILABLE));
      return node;
    }
    if (typeof provenance.verbatim_snippet === "string" && provenance.verbatim_snippet !== "") {
      node.appendChild(el("p", "os-prov-caption", "Verbatim snippet from the source document"));
      node.appendChild(el("blockquote", "os-prov-snippet", provenance.verbatim_snippet));
    } else {
      node.appendChild(el("p", "os-prov-unavailable", EVIDENCE_UNAVAILABLE));
    }
    var rows = locationRows(provenance.location);
    if (rows === null) {
      node.appendChild(el("p", "os-prov-unavailable", LOCATION_UNAVAILABLE));
    } else {
      node.appendChild(definitionList(rows));
    }
    return node;
  }

  function renderMatching(matching) {
    var node = section("Semantic matching");
    node.appendChild(definitionList([
      ["Matched SKU", matching.matched_sku, "os-prov-mono"],
      ["Product", matching.sku_name],
      ["Confidence", matching.sku_confidence],
      ["Resolution", matching.sku_resolution_source, "os-prov-mono"]
    ]));
    if (!isMissing(displayText(matching.matching_rationale))) {
      node.appendChild(el("p", "os-prov-caption os-prov-rationale-label", "Matching rationale"));
      node.appendChild(el("p", "os-prov-rationale", displayText(matching.matching_rationale)));
    }
    return node;
  }

  /** Read-only listing; candidate selection belongs to P2. */
  function renderCandidates(candidates) {
    var node = section("Candidate SKUs (read-only)");
    var list = el("ul", "os-prov-candidates");
    candidates.forEach(function (candidate) {
      var item = el("li");
      if (!isObject(candidate)) {
        item.appendChild(valueNode("span", candidate, "os-prov-mono"));
      } else {
        item.appendChild(valueNode("span", candidate.sku, "os-prov-mono"));
        ["name", "rationale"].forEach(function (key) {
          if (displayText(candidate[key]) !== "") {
            item.appendChild(el("span", "os-prov-sub", displayText(candidate[key])));
          }
        });
        if (displayText(candidate.score) !== "") {
          item.appendChild(el("span", "os-prov-sub", "Score: " + displayText(candidate.score)));
        }
      }
      list.appendChild(item);
    });
    node.appendChild(list);
    return node;
  }

  function render(payload) {
    var provenance = isObject(payload.provenance) ? payload.provenance : null;
    var matching = isObject(payload.matching) ? payload.matching : null;

    nodes.title.textContent = displayText(payload.label) || "Field provenance";
    var sections = [renderField(payload, provenance), renderEvidence(provenance)];
    if (matching !== null) {
      sections.push(renderMatching(matching));
      if (Array.isArray(matching.candidate_skus) && matching.candidate_skus.length > 0) {
        sections.push(renderCandidates(matching.candidate_skus));
      }
    }
    nodes.body.replaceChildren.apply(nodes.body, sections);
    nodes.body.scrollTop = 0;
  }

  // ---------- Mounting / lifecycle ----------

  function mount() {
    if (nodes !== null) {
      return;
    }
    if (!document.getElementById(STYLE_ID)) {
      var style = el("style", "", CSS);
      style.id = STYLE_ID;
      document.head.appendChild(style);
    }

    var backdrop = el("div", "os-prov-backdrop");
    backdrop.hidden = true;
    backdrop.addEventListener("click", close);

    var drawer = el("aside", "os-prov-drawer");
    drawer.hidden = true;
    drawer.setAttribute("role", "dialog");
    drawer.setAttribute("aria-modal", "true");
    drawer.setAttribute("aria-labelledby", TITLE_ID);

    var head = el("header", "os-prov-head");
    var heading = el("div");
    heading.style.minWidth = "0";
    heading.appendChild(el("p", "os-prov-eyebrow", "Field provenance"));
    var title = el("h2", "os-prov-title");
    title.id = TITLE_ID;
    heading.appendChild(title);
    var closeButton = el("button", "os-prov-close", "Close");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "Close field provenance");
    closeButton.addEventListener("click", close);
    head.appendChild(heading);
    head.appendChild(closeButton);

    // Focusable so keyboard users can scroll long evidence.
    var body = el("div", "os-prov-body");
    body.tabIndex = 0;

    drawer.appendChild(head);
    drawer.appendChild(body);
    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);
    nodes = { backdrop: backdrop, drawer: drawer, title: title, closeButton: closeButton, body: body };
  }

  /** Starts the drawer below a visible replay banner; the banner itself is only measured, never changed. */
  function position() {
    if (nodes === null) {
      return;
    }
    var top = 0;
    var banner = document.querySelector(REPLAY_BANNER_SELECTOR);
    if (banner && !banner.hidden) {
      var rect = banner.getBoundingClientRect();
      if (rect.height > 0 && rect.bottom > 0 && rect.bottom < window.innerHeight / 2) {
        top = Math.round(rect.bottom);
      }
    }
    nodes.drawer.style.top = top + "px";
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
    // Simple local focus loop between the two focusable elements.
    var focusable = [nodes.closeButton, nodes.body];
    var index = focusable.indexOf(document.activeElement);
    var next = event.shiftKey
      ? (index <= 0 ? focusable.length - 1 : index - 1)
      : (index === -1 || index === focusable.length - 1 ? 0 : index + 1);
    event.preventDefault();
    focusable[next].focus();
  }

  function openDrawer(payload) {
    mount();
    if (!open) {
      previousFocus = document.activeElement;
      document.addEventListener("keydown", onKeydown, true);
      window.addEventListener("scroll", position, true);
      window.addEventListener("resize", position);
    }
    render(isObject(payload) ? payload : {});
    nodes.backdrop.hidden = false;
    nodes.drawer.hidden = false;
    open = true;
    position();
    nodes.closeButton.focus();
  }

  function close() {
    if (!open) {
      return;
    }
    open = false;
    document.removeEventListener("keydown", onKeydown, true);
    window.removeEventListener("scroll", position, true);
    window.removeEventListener("resize", position);
    nodes.backdrop.hidden = true;
    nodes.drawer.hidden = true;
    nodes.body.replaceChildren();
    var target = previousFocus;
    previousFocus = null;
    if (target && typeof target.focus === "function" && document.contains(target)) {
      target.focus();
    }
  }

  function destroy() {
    close();
    if (nodes !== null) {
      nodes.backdrop.remove();
      nodes.drawer.remove();
      nodes = null;
    }
    var style = document.getElementById(STYLE_ID);
    if (style) {
      style.remove();
    }
  }

  window.OrderShieldProvenanceDrawer = {
    open: openDrawer,
    close: close,
    isOpen: function () {
      return open;
    },
    destroy: destroy
  };
})();
