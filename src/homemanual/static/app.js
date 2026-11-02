"use strict";

const state = {
  meta: null,
  documents: [],
  appliances: [],
  activeApplianceId: "",
  busy: false,
};

const EXAMPLES = [
  { question: "How do I clean the lint filter?", category: "dryer" },
  { question: "How do I replace the water filter?", category: "refrigerator" },
  { question: "How do I clean the dishwasher filter?", category: "dishwasher" },
  { question: "How do I clean the inside of the microwave?", category: "microwave" },
  { question: "How do I level the washer?", category: "washer" },
];

const CITATION_RE = /\[(S\d+)\]/g;
const SVG_NS = "http://www.w3.org/2000/svg";

const ICONS = {
  search: ["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14", "M16.5 16.5 21 21"],
  plus: ["M12 5v14", "M5 12h14"],
  check: ["M20 6 9 17l-5-5"],
  pencil: ["M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"],
  trash: ["M3 6h18", "M8 6V4h8v2", "M6 6l1 14h10l1-14"],
};

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  (ICONS[name] || []).forEach((d) => {
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  });
  return svg;
}

function el(tag, props, children) {
  const node = document.createElement(tag);
  if (props) {
    Object.entries(props).forEach(([key, value]) => {
      if (value === undefined || value === null) return;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "onclick" || key === "onsubmit" || key === "onchange") {
        node.addEventListener(key.slice(2), value);
      } else node.setAttribute(key, value);
    });
  }
  (children || []).forEach((child) => node.appendChild(child));
  return node;
}

function iconButton(name, label, handler, extraClass) {
  const button = el("button", {
    class: `icon-btn${extraClass ? ` ${extraClass}` : ""}`,
    type: "button",
    "aria-label": label,
    title: label,
  });
  button.appendChild(icon(name));
  button.addEventListener("click", handler);
  return button;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function selectedOptionText(select) {
  return select.options[select.selectedIndex]
    ? select.options[select.selectedIndex].text.split(" (")[0]
    : "";
}

async function jsonRequest(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = { error: `unreadable response (${response.status})` };
  }
  return { ok: response.ok, status: response.status, data };
}

// ---------------------------------------------------------------- boot
async function loadMeta() {
  const response = await fetch("/api/meta");
  if (!response.ok) throw new Error(`meta request failed (${response.status})`);
  state.meta = await response.json();
  state.documents = state.meta.documents || [];
  state.appliances = (state.meta.household && state.meta.household.appliances) || [];
  renderChips();
  fillCategories();
  fillApplianceCategories();
  fillModels();
  fillApplianceModels();
  fillApplianceSelect();
  renderAppliances();
  renderExamples();
  renderLibrary();
  renderBlocked();
  renderGenerationToggle();
  document.getElementById("footer-status").textContent =
    `${state.documents.length} manuals indexed · index built ${state.meta.index_built_at || "unknown"}`;
}

function renderChips() {
  const chips = document.getElementById("status-chips");
  clear(chips);
  chips.appendChild(el("span", { class: "chip on", text: `${state.documents.length} manuals` }));
  const counts = {};
  state.documents.forEach((doc) => {
    counts[doc.category_label] = (counts[doc.category_label] || 0) + 1;
  });
  Object.entries(counts).forEach(([label, value]) => {
    chips.appendChild(el("span", { class: "chip", text: `${label} ${value}` }));
  });
  const generation = state.meta.generation || {};
  chips.appendChild(
    el("span", {
      class: generation.status === "enabled" ? "chip on" : "chip off",
      text:
        generation.status === "enabled"
          ? "DeepSeek answers on"
          : "Manual search only (no model calls)",
    })
  );
  if (state.meta.warnings && state.meta.warnings.length) {
    chips.appendChild(
      el("span", { class: "chip off", text: `${state.meta.warnings.length} index warning(s)` })
    );
  }
}

// ------------------------------------------------------------ My home
function fillApplianceCategories() {
  const select = document.getElementById("appliance-category");
  clear(select);
  (state.meta.categories || []).forEach((item) => {
    select.appendChild(el("option", { value: item.id, text: item.label }));
  });
}

function fillApplianceModels() {
  const category = document.getElementById("appliance-category").value;
  const select = document.getElementById("appliance-model");
  clear(select);
  const docs = state.documents.filter((doc) => doc.category === category && doc.models.length);
  docs.forEach((doc) => {
    const group = el("optgroup", {
      label: `${doc.brand} — ${doc.models.length} curated model(s)`,
    });
    doc.models.forEach((model) => {
      group.appendChild(el("option", { value: model, text: model }));
    });
    select.appendChild(group);
  });
  const hasModels = docs.length > 0;
  select.disabled = !hasModels;
  document.getElementById("appliance-add").disabled = !hasModels;
  if (!hasModels) {
    select.appendChild(el("option", { value: "", text: "No manual for this type" }));
  }
}

function renderAppliances() {
  const list = document.getElementById("appliance-list");
  clear(list);
  document.getElementById("home-count").textContent =
    state.appliances.length === 1 ? "1 saved" : `${state.appliances.length} saved`;
  if (!state.appliances.length) {
    list.appendChild(
      el("li", {
        class: "empty",
        text: "No appliances saved yet. Add one above and questions will be answered from that exact model's manual.",
      })
    );
    return;
  }
  state.appliances.forEach((appliance) => {
    const active = appliance.id === state.activeApplianceId;
    const item = el("li", { class: active ? "appliance active" : "appliance" });
    const body = el("div", { class: "appliance-body" });
    body.appendChild(
      el("div", {
        class: "appliance-name",
        text: `${appliance.nickname}${active ? "  ·  in use" : ""}`,
      })
    );
    body.appendChild(
      el("div", {
        class: "appliance-meta",
        text: `${appliance.brand} ${appliance.category_label} · ${appliance.model}`,
      })
    );
    if (!appliance.supported) {
      body.appendChild(
        el("div", {
          class: "appliance-warn",
          text: "No matching manual in the library right now, so this one cannot be used.",
        })
      );
    }
    item.appendChild(body);

    const actions = el("div", { class: "appliance-actions" });
    if (appliance.supported) {
      actions.appendChild(
        iconButton("check", active ? "Already in use" : "Use this appliance", () =>
          selectAppliance(appliance.id)
        )
      );
    }
    actions.appendChild(
      iconButton("pencil", "Rename this appliance", () => renameAppliance(appliance))
    );
    actions.appendChild(
      iconButton("trash", "Remove this saved appliance", () => removeAppliance(appliance))
    );
    item.appendChild(actions);
    list.appendChild(item);
  });
}

function applianceStatus(kind, message) {
  const box = document.getElementById("appliance-status");
  clear(box);
  if (message) box.appendChild(banner(kind, message));
}

async function addAppliance(event) {
  event.preventDefault();
  const nickname = document.getElementById("appliance-name").value;
  const category = document.getElementById("appliance-category").value;
  const model = document.getElementById("appliance-model").value;
  if (!nickname.trim()) {
    applianceStatus("warn", "Give the appliance a name first.");
    document.getElementById("appliance-name").focus();
    return;
  }
  const { ok, data } = await jsonRequest("/api/appliances/add", { nickname, category, model });
  if (!ok) {
    applianceStatus("warn", data.error || "That appliance could not be saved.");
    return;
  }
  state.appliances = data.appliances || [];
  document.getElementById("appliance-name").value = "";
  selectAppliance(data.appliance.id);
  applianceStatus("", `Saved ${data.appliance.nickname}. Questions now use that manual.`);
}

async function renameAppliance(appliance) {
  const proposed = window.prompt("New name for this appliance:", appliance.nickname);
  if (proposed === null) return;
  const { ok, data } = await jsonRequest("/api/appliances/rename", {
    id: appliance.id,
    nickname: proposed,
  });
  if (!ok) {
    applianceStatus("warn", data.error || "That name could not be saved.");
    return;
  }
  state.appliances = data.appliances || [];
  renderAppliances();
  fillApplianceSelect();
  applianceStatus("", "Name updated.");
}

async function removeAppliance(appliance) {
  const confirmed = window.confirm(
    `Remove the saved appliance "${appliance.nickname}"? The manual PDFs stay in the library.`
  );
  if (!confirmed) return;
  const { ok, data } = await jsonRequest("/api/appliances/remove", { id: appliance.id });
  if (!ok) {
    applianceStatus("warn", data.error || "That appliance could not be removed.");
    return;
  }
  state.appliances = data.appliances || [];
  if (state.activeApplianceId === appliance.id) state.activeApplianceId = "";
  renderAppliances();
  fillApplianceSelect();
  applianceStatus("", "Removed. The manual PDFs were not touched.");
}

function clearManualFilters() {
  const category = document.getElementById("category");
  const model = document.getElementById("model");
  category.value = "";
  fillModels();
  model.value = "";
}

function selectAppliance(id) {
  const wasBound = Boolean(state.activeApplianceId);
  state.activeApplianceId = id || "";
  // Switching back to "Whole library" must not leave the previous appliance's
  // type and model silently applied to the next question.
  if (!state.activeApplianceId && wasBound) clearManualFilters();
  fillApplianceSelect();
  renderAppliances();
}

async function refreshAppliances() {
  // Re-read the saved list and the library so a stale selection can be dropped.
  const response = await fetch("/api/meta");
  if (!response.ok) return false;
  const meta = await response.json();
  state.meta = meta;
  state.documents = meta.documents || [];
  state.appliances = (meta.household && meta.household.appliances) || [];
  if (!state.appliances.some((item) => item.id === state.activeApplianceId)) {
    state.activeApplianceId = "";
    clearManualFilters();
  }
  fillModels();
  fillApplianceSelect();
  renderAppliances();
  return true;
}

// ------------------------------------------------------------ ask panel
function fillCategories() {
  const select = document.getElementById("category");
  clear(select);
  select.appendChild(el("option", { value: "", text: "Any appliance" }));
  (state.meta.categories || []).forEach((item) => {
    const count = state.documents.filter((doc) => doc.category === item.id).length;
    select.appendChild(
      el("option", {
        value: item.id,
        text: `${item.label} (${count} manual${count === 1 ? "" : "s"})`,
      })
    );
  });
}

function fillModels() {
  const category = document.getElementById("category").value;
  const select = document.getElementById("model");
  clear(select);
  select.appendChild(el("option", { value: "", text: "Any model in the library" }));
  const docs = state.documents.filter((doc) => !category || doc.category === category);
  docs.forEach((doc) => {
    if (!doc.models.length) return;
    const group = el("optgroup", {
      label: `${doc.brand} ${doc.category_label} — ${doc.models.length} model(s)`,
    });
    doc.models.forEach((model) => {
      group.appendChild(el("option", { value: model, text: model }));
    });
    select.appendChild(group);
  });
}

function fillApplianceSelect() {
  const select = document.getElementById("appliance");
  const previous = state.activeApplianceId;
  clear(select);
  select.appendChild(
    el("option", { value: "", text: "Whole library (search every manual)" })
  );
  state.appliances.forEach((appliance) => {
    const suffix = appliance.supported ? "" : " (no manual)";
    select.appendChild(
      el("option", {
        value: appliance.id,
        text: `${appliance.nickname} — ${appliance.model}${suffix}`,
        disabled: appliance.supported ? undefined : "disabled",
      })
    );
  });
  select.value = previous;
  applyApplianceBinding();
}

function activeAppliance() {
  return state.appliances.find((item) => item.id === state.activeApplianceId) || null;
}

function applyApplianceBinding() {
  const appliance = activeAppliance();
  const category = document.getElementById("category");
  const model = document.getElementById("model");
  const row = document.getElementById("filter-row");
  const hint = document.getElementById("binding-hint");
  if (appliance) {
    category.value = appliance.category;
    fillModels();
    model.value = appliance.model;
    category.disabled = true;
    model.disabled = true;
    row.classList.add("bound");
    if (!hint) {
      row.appendChild(
        el("p", {
          class: "binding-hint",
          id: "binding-hint",
          text: "Bound to this saved appliance: answers stay inside its manual.",
        })
      );
    }
  } else {
    category.disabled = false;
    model.disabled = false;
    row.classList.remove("bound");
    if (hint) hint.remove();
  }
}

function renderExamples() {
  const wrap = document.getElementById("examples");
  clear(wrap);
  EXAMPLES.forEach((example) => {
    wrap.appendChild(
      el("button", {
        class: "example",
        type: "button",
        text: example.question,
        onclick: () => {
          selectAppliance("");
          document.getElementById("category").value = example.category || "";
          fillModels();
          document.getElementById("question").value = example.question;
          document.getElementById("question").focus();
        },
      })
    );
  });
}

function renderGenerationToggle() {
  const wrap = document.getElementById("generate-wrap");
  const generation = state.meta.generation || {};
  if (generation.status === "enabled") {
    wrap.hidden = false;
  } else {
    wrap.hidden = true;
    document.getElementById("generate").checked = false;
  }
}

// ------------------------------------------------------------- library
function renderLibrary() {
  const list = document.getElementById("library-list");
  clear(list);
  (state.meta.categories || []).forEach((category) => {
    const docs = state.documents.filter((doc) => doc.category === category.id);
    if (!docs.length) return;
    list.appendChild(el("div", { class: "lib-title", text: category.label }));
    docs.forEach((doc) => {
      const item = el("div", { class: "lib-item" });
      item.appendChild(el("div", { class: "lib-title", text: `${doc.brand} — ${doc.title}` }));
      const missing = (doc.pages_without_text || []).length;
      const summary = `${doc.pages} pages · downloaded ${(doc.retrieved_at || "").slice(0, 10)}`;
      item.appendChild(
        el("div", {
          class: "lib-meta",
          text: missing ? `${summary} · ${missing} page(s) with no extractable text` : summary,
        })
      );
      const row = el("div", { class: "link-row" });
      row.appendChild(
        el("a", { href: doc.url, target: "_blank", rel: "noopener", text: "Open manual PDF" })
      );
      if (doc.source_page_url) {
        row.appendChild(
          el("a", {
            href: doc.source_page_url,
            target: "_blank",
            rel: "noopener noreferrer",
            text: "Manufacturer support page",
          })
        );
      }
      item.appendChild(row);
      const details = el("details", { class: "source-details" });
      details.appendChild(el("summary", { text: "Source details" }));
      details.appendChild(
        el("div", {
          class: "lib-meta",
          text: `manual id ${doc.doc_id} · sha256 ${doc.sha256} · retrieved ${doc.retrieved_at || "unknown"}`,
        })
      );
      details.appendChild(
        el("div", {
          class: "lib-meta",
          text: `curated models: ${doc.models.join(", ") || "none recorded"}`,
        })
      );
      if (missing) {
        details.appendChild(
          el("div", {
            class: "lib-meta",
            text: `pages with no extractable text (blank or image-only): ${(doc.pages_without_text || []).join(", ")}`,
          })
        );
      }
      item.appendChild(details);
      list.appendChild(item);
    });
  });
  document.getElementById("library-summary").textContent =
    `${state.documents.length} official manuals from ${new Set(state.documents.map((d) => d.brand)).size} brands. ` +
    "Each passage links to its real PDF page.";
}

function renderBlocked() {
  const details = document.getElementById("blocked-details");
  const list = document.getElementById("blocked-list");
  const blocked = state.meta.blocked_sources || [];
  clear(list);
  if (!blocked.length) {
    details.hidden = true;
    return;
  }
  blocked.forEach((item) => {
    list.appendChild(el("li", { text: `${item.brand} ${item.category}: ${item.reason}` }));
  });
  details.hidden = false;
}

// -------------------------------------------------------------- result
function banner(kind, text) {
  return el("div", { class: `status-banner ${kind}`, text });
}

function citationNode(text) {
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  text.replace(CITATION_RE, (match, id, offset) => {
    if (offset > cursor) fragment.appendChild(document.createTextNode(text.slice(cursor, offset)));
    fragment.appendChild(el("a", { class: "cite", href: `#source-${id}`, text: id }));
    cursor = offset + match.length;
    return match;
  });
  if (cursor < text.length) fragment.appendChild(document.createTextNode(text.slice(cursor)));
  return fragment;
}

function evidenceCard(item) {
  const card = el("article", { class: "card", id: `source-${item.source_id}` });
  const head = el("div", { class: "card-head" });
  head.appendChild(
    el("div", {
      class: "card-title",
      text: `${item.source_id} · ${item.brand} ${item.category} — ${item.title}`,
    })
  );
  const printed = item.printed_page ? ` · printed page ${item.printed_page}` : "";
  head.appendChild(
    el("div", {
      class: "card-meta",
      text: `PDF page ${item.pdf_page}${printed}${item.section ? ` · ${item.section}` : ""}`,
    })
  );
  card.appendChild(head);
  if (item.has_warning) {
    card.appendChild(el("span", { class: "tag warn-tag", text: "contains a safety notice" }));
  }
  const passage = el("p", { class: "passage", text: item.text });
  card.appendChild(passage);
  card.appendChild(
    el("button", {
      class: "scroll-toggle",
      type: "button",
      text: "Show full passage",
      onclick: (event) => {
        passage.classList.toggle("open");
        event.target.textContent = passage.classList.contains("open")
          ? "Hide full passage"
          : "Show full passage";
      },
    })
  );
  const row = el("div", { class: "link-row" });
  row.appendChild(
    el("a", {
      href: item.url,
      target: "_blank",
      rel: "noopener",
      text: `Open PDF page ${item.pdf_page}`,
    })
  );
  if (item.source_page_url) {
    row.appendChild(
      el("a", {
        href: item.source_page_url,
        target: "_blank",
        rel: "noopener noreferrer",
        text: "Manufacturer support page",
      })
    );
  }
  card.appendChild(row);
  return card;
}

function renderClarification(result, data) {
  const clarification = data.clarification || {};
  result.appendChild(banner("warn", clarification.message || "This needs a model number."));
  const options = clarification.options || [];
  if (options.length) {
    const wrap = el("div", { class: "option-list" });
    options.forEach((option) => {
      wrap.appendChild(
        el("button", {
          class: "option",
          type: "button",
          text: `${option.brand} ${option.models[0] || option.title}`,
          onclick: () => {
            selectAppliance("");
            document.getElementById("category").value = option.category;
            fillModels();
            document.getElementById("model").value = option.models[0] || "";
            submitQuestion();
          },
        })
      );
    });
    result.appendChild(wrap);
  }
  appendNotes(result, data.notes);
}

async function renderResponse(data) {
  const result = document.getElementById("result");
  clear(result);

  if (data.status === "needs_clarification") {
    renderClarification(result, data);
    return;
  }
  if (data.status === "unknown_model") {
    result.appendChild(
      banner(
        "warn",
        `No manual for ${(data.unknown_models || []).join(", ")} in this library. Only a ` +
          "manual whose model list contains that exact model is searched, so no other manual " +
          "was used and there is no answer to show."
      )
    );
    const options = (data.clarification && data.clarification.options) || [];
    if (options.length) {
      const wrap = el("div", { class: "option-list" });
      options.forEach((option) => {
        wrap.appendChild(
          el("button", {
            class: "option",
            type: "button",
            text: `${option.brand} ${option.models[0] || option.title}`,
            onclick: () => {
              document.getElementById("category").value = option.category;
              fillModels();
              document.getElementById("model").value = option.models[0] || "";
              submitQuestion();
            },
          })
        );
      });
      result.appendChild(wrap);
    }
    appendNotes(result, data.notes);
    return;
  }
  if (data.status === "conflicting_models") {
    result.appendChild(
      banner(
        "warn",
        "The selected model and the model in the question belong to different manuals, so " +
          "nothing was answered."
      )
    );
    appendNotes(result, data.notes);
    return;
  }
  if (data.status === "unknown_appliance" || data.status === "unsupported_appliance") {
    result.appendChild(
      banner(
        "warn",
        (data.notes && data.notes[0]) || "That saved appliance cannot be used."
      )
    );
    // The saved appliance is gone or no longer supported: drop the stale
    // binding straight away and re-read the list so the next question works.
    state.activeApplianceId = "";
    clearManualFilters();
    fillApplianceSelect();
    renderAppliances();
    try {
      await refreshAppliances();
    } catch (error) {
      /* keep the local state - the picker is still usable */
    }
    return;
  }
  if (data.status === "no_evidence") {
    result.appendChild(
      banner("warn", "No passage in the downloaded manuals matched that question.")
    );
    appendNotes(result, data.notes);
    return;
  }
  if (data.status === "bad_request" || data.status === "unknown_document") {
    result.appendChild(
      banner("warn", (data.notes && data.notes[0]) || "That request was not valid.")
    );
    return;
  }

  const scopeBits = new Set();
  if (data.appliance) {
    scopeBits.add(`saved appliance ${data.appliance.nickname} (${data.appliance.model})`);
  }
  const categorySelect = document.getElementById("category");
  if (!data.appliance && categorySelect.value) {
    scopeBits.add(selectedOptionText(categorySelect));
  }
  const modelSelect = document.getElementById("model");
  if (!data.appliance && modelSelect.value) scopeBits.add(`model ${modelSelect.value}`);
  (data.matched_models || []).forEach((item) => scopeBits.add(`model ${item.query}`));
  if (data.implied_category) scopeBits.add(data.implied_category);
  const scope = scopeBits.size
    ? `Scope: ${[...scopeBits].join(", ")}.`
    : "Scope: whole library.";
  result.appendChild(banner("ok", `${data.evidence.length} passage(s) found. ${scope}`));

  const generation = data.generation || {};
  if (data.mode === "generated_answer" && data.answer) {
    const box = el("div", { class: "answer" });
    box.appendChild(el("h3", { text: "Written answer (DeepSeek)" }));
    box.appendChild(
      el("p", {
        class: "card-meta",
        text:
          "Each sentence cites a passage and quotes words that appear in it. That checks the " +
          "citations and the quotes, not whether the sentence is a faithful reading, so compare " +
          "it with the passages below.",
      })
    );
    const paragraph = el("p");
    paragraph.appendChild(citationNode(data.answer));
    box.appendChild(paragraph);
    const pages = (generation.result && generation.result.citations) || [];
    if (pages.length) {
      const used = data.evidence
        .filter((item) => pages.includes(item.source_id))
        .map((item) => `${item.source_id} = PDF page ${item.pdf_page}`)
        .join(" · ");
      if (used) box.appendChild(el("p", { class: "card-meta", text: `Supporting pages: ${used}` }));
    }
    result.appendChild(box);
  } else if (generation.requested && generation.result) {
    const detail = generation.result.detail;
    if (detail) {
      result.appendChild(
        banner("warn", `No written answer was shown. ${detail}`)
      );
    }
  } else if (generation.requested && generation.status !== "enabled") {
    result.appendChild(banner("", generation.detail));
  }

  appendNotes(result, data.notes);

  const list = el("div", { class: "evidence" });
  data.evidence.forEach((item) => list.appendChild(evidenceCard(item)));
  result.appendChild(list);
}

function appendNotes(container, notes) {
  (notes || []).forEach((note) => container.appendChild(banner("", note)));
}

async function submitQuestion() {
  if (state.busy) return;
  const question = document.getElementById("question").value.trim();
  const result = document.getElementById("result");
  if (!question) {
    clear(result);
    result.appendChild(banner("warn", "Type a question first."));
    return;
  }
  const appliance = state.activeApplianceId ? activeAppliance() : null;
  const button = document.getElementById("ask-button");
  state.busy = true;
  button.disabled = true;
  clear(result);
  result.appendChild(
    el("div", { class: "loading" }, [
      el("span", { class: "spinner" }),
      el("span", { text: "Searching the manuals…" }),
    ])
  );
  try {
    const { data } = await jsonRequest("/api/ask", {
      question,
      appliance_id: appliance ? appliance.id : null,
      category: appliance ? null : document.getElementById("category").value || null,
      model: appliance ? null : document.getElementById("model").value || null,
      generate: document.getElementById("generate").checked,
    });
    await renderResponse(data);
  } catch (error) {
    clear(result);
    result.appendChild(banner("warn", `Request failed: ${error.message}`));
  } finally {
    state.busy = false;
    button.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("category").addEventListener("change", () => {
    fillModels();
    applyApplianceBinding();
  });
  document.getElementById("appliance-category").addEventListener("change", fillApplianceModels);
  document.getElementById("appliance").addEventListener("change", (event) =>
    selectAppliance(event.target.value)
  );
  document.getElementById("appliance-form").addEventListener("submit", addAppliance);
  document.getElementById("ask-form").addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuestion();
  });
  loadMeta()
    .then(() => {
      const params = new URLSearchParams(window.location.search);
      const applianceId = params.get("appliance");
      const category = params.get("category");
      const model = params.get("model");
      const question = params.get("q");
      if (category) {
        document.getElementById("category").value = category;
        fillModels();
      }
      if (model) document.getElementById("model").value = model;
      if (applianceId && state.appliances.some((item) => item.id === applianceId)) {
        selectAppliance(applianceId);
      }
      if (question) {
        document.getElementById("question").value = question;
        submitQuestion();
      }
    })
    .catch((error) => {
      const result = document.getElementById("result");
      result.appendChild(banner("warn", `Could not load the library: ${error.message}`));
    });
});
