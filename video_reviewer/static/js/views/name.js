// Stage 2 — Name. A vision model looks at sample frames from each clip and
// fills in the description and client, which the operator confirms in stage 3.

import { api } from "../api.js";
import { el, clear, busy, pill, note } from "../dom.js";
import { state, remember, loadRows, counts } from "../store.js";

const FRAMES_PER_PRESET = { fast: 6, balanced: 12, accurate: 16 };

export async function nameView({ view, dock }) {
  const messages = el("div");
  const providerSelect = el("select", { id: "provider" });
  const keySlot = el("div");
  const estimate = el("p.hint");
  const results = el("div.plan");

  let status = null;
  let running = false;

  const presetSelect = el(
    "select",
    { id: "preset", onchange: (event) => (remember("preset", event.target.value), refreshEstimate()) },
    el("option", { value: "fast" }, "Fast — fewest frames, lowest cost"),
    el("option", { value: "balanced" }, "Balanced — recommended"),
    el("option", { value: "accurate" }, "Thorough — most frames"),
  );
  presetSelect.value = state.preset;

  const modelOptions = el("datalist", { id: "modelOptions" });
  const modelInput = el("input", { type: "text", id: "model", list: "modelOptions", placeholder: "provider default" });

  const runButton = el("button.btn.primary", { onclick: () => run() }, "Name every clip");

  // ── provider status ───────────────────────────────────────────────────────

  function refreshEstimate() {
    const pending = counts().pending + counts().blocked;
    const per = FRAMES_PER_PRESET[presetSelect.value] || 12;
    estimate.textContent = pending
      ? `${pending} clip${pending === 1 ? "" : "s"} left to name — up to ${pending * per} frames sent.`
      : "Every clip in this batch already has a name. Running again re-reads the ones you reset.";
  }

  function renderKeySlot() {
    clear(keySlot);
    const names = (status.env_key_names || []).join(" or ");

    if (status.provider === "codex-proxy") {
      keySlot.append(
        el("span.pill.approved", null, "Protected local connection"),
        el("p.hint", null, "Video Renamer generated a private loopback key automatically. Your Codex sign-in stays with the official Codex CLI."),
      );
      return;
    }

    if (status.saved_key) {
      keySlot.append(
        el(
          "div",
          { style: { display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" } },
          el("span.pill.approved", null, `key saved ${status.key_hint}`),
          el(
            "button.btn.link",
            {
              onclick: async () => {
                await api.forgetKey(state.provider);
                await refreshStatus();
              },
            },
            "Remove this key",
          ),
        ),
        el("p.hint", null, "Stored on this machine only, in a private file readable by your account."),
      );
      return;
    }

    const keyInput = el("input", {
      type: "password",
      id: "apikey",
      autocomplete: "off",
      placeholder: names ? `paste key, or set ${names}` : "paste provider API key",
    });
    const saveBox = el("input", { type: "checkbox", id: "rememberKey", checked: true });

    keySlot.append(
      el("div.field", null, el("label", { for: "apikey" }, "API key"), keyInput),
      el(
        "label.checkline",
        { for: "rememberKey", style: { marginTop: "10px" } },
        saveBox,
        "Remember this key on this machine",
      ),
      el(
        "p.hint",
        null,
        status.needs_key
          ? "The key is sent to the provider with each request and is never written into the manifest."
          : status.message,
      ),
    );
    keySlot.keyInput = keyInput;
    keySlot.saveBox = saveBox;
  }

  async function refreshStatus() {
    try {
      status = await api.aiStatus(state.provider);
    } catch (error) {
      clear(messages).append(note("err", error.message));
      return;
    }

    clear(providerSelect);
    for (const provider of status.providers || []) {
      providerSelect.append(el("option", { value: provider.id }, provider.display_name));
    }
    providerSelect.value = status.provider;
    modelInput.placeholder = status.default_model || "provider default";
    clear(modelOptions);
    const models = status.models || [];
    for (const model of models.length ? models : [status.default_model, status.cheap_model, status.accurate_model]) {
      if (model) modelOptions.append(el("option", { value: model }));
    }

    renderKeySlot();
    refreshEstimate();

    clear(messages);
    if (!status.available) messages.append(note("err", status.message));
    else if (status.needs_key && !status.saved_key) messages.append(note("warn", status.message));

    const left = counts().pending + counts().blocked;
    clear(runButton).append(
      left ? `Name ${left} clip${left === 1 ? "" : "s"}` : "Every clip is named",
    );
    runButton.disabled = running || !status.available || !left;
  }

  providerSelect.addEventListener("change", async (event) => {
    remember("provider", event.target.value);
    await refreshStatus();
  });

  // ── run ───────────────────────────────────────────────────────────────────

  async function run() {
    const targets = state.rows
      .map((row, index) => ({ row, index }))
      .filter(({ row }) => ["pending", "blocked"].includes(row.review_status))
      .map(({ index }) => index);

    if (!targets.length) {
      clear(messages).append(note("info", "Nothing left to name. Move on to Review."));
      return;
    }

    const typedKey = keySlot.keyInput ? keySlot.keyInput.value.trim() : "";
    if (status.needs_key && !status.saved_key && !typedKey) {
      clear(messages).append(note("err", "Paste an API key before naming, or set it in your environment."));
      return;
    }
    if (typedKey && keySlot.saveBox && keySlot.saveBox.checked) {
      await api.saveKey(state.provider, typedKey);
    }

    running = true;
    runButton.disabled = true;
    clear(results);
    clear(messages);

    let named = 0;
    for (const [position, index] of targets.entries()) {
      const row = state.rows[index];
      const line = el(
        "div.r",
        null,
        el("span.from", { title: row.source_name }, row.source_name),
        el("span.arrow", null, "→"),
        el("span.to", null, pill("working")),
      );
      results.append(line);
      clear(messages).append(note("info", `Reading clip ${position + 1} of ${targets.length}…`));

      try {
        const response = await api.aiReview({
          indices: [index],
          provider: state.provider,
          model: modelInput.value.trim(),
          api_key: typedKey,
          preset: presetSelect.value,
        });
        const result = (response.results || [])[0] || {};
        clear(line.lastChild);
        if (result.proposed_name) {
          line.lastChild.append(result.proposed_name);
          named += 1;
        } else {
          line.lastChild.append(pill(result.status || "needs_review"), " ", result.error || "needs your eyes");
        }
      } catch (error) {
        clear(line.lastChild).append(pill("blocked"), " ", error.message);
      }
    }

    await loadRows();
    running = false;
    await refreshStatus();

    clear(messages).append(
      named === targets.length
        ? note("ok", `Named ${named} of ${targets.length}. Check them in Review.`)
        : note("warn", `Named ${named} of ${targets.length}. The rest need naming by hand in Review.`),
    );
    renderDock();
  }

  // ── naming guide ──────────────────────────────────────────────────────────
  // The single lever for fixing names that come out consistently wrong.

  function guidePanel() {
    const editor = el("textarea.guide", { spellcheck: false });
    const path = el("p.guide-path", null, "loading…");
    const status = el("span.tally");

    api.namingGuide().then((guide) => {
      editor.value = guide.text;
      path.textContent = guide.path;
    }).catch((error) => {
      path.textContent = error.message;
    });

    const saveButton = el(
      "button.btn",
      {
        onclick: () =>
          busy(saveButton, "Saving…", () => api.saveNamingGuide(editor.value))
            .then(() => (status.textContent = "Saved. The next review uses it."))
            .catch((error) => (status.textContent = error.message)),
      },
      "Save guide",
    );

    const resetButton = el(
      "button.btn.link",
      {
        onclick: () =>
          api.resetNamingGuide().then((guide) => {
            editor.value = guide.text;
            status.textContent = "Reset to the SOP default.";
          }),
      },
      "Reset to the SOP default",
    );

    return el(
      "section.panel",
      null,
      el(
        "details",
        null,
        el("summary", null, "Naming guide"),
        el(
          "p",
          { style: { color: "var(--dim)", fontSize: "13px", margin: "10px 0 14px" } },
          "Sent with every request and followed over the model's own instincts. If names keep " +
            "coming out wrong — a client it misspells, a term it invents, a rule it ignores — " +
            "write the correction here.",
        ),
        editor,
        el(
          "div",
          { style: { display: "flex", alignItems: "center", gap: "12px", marginTop: "12px", flexWrap: "wrap" } },
          saveButton,
          resetButton,
          status,
        ),
        el("p", { style: { marginTop: "10px" } }, el("span.guide-path", null, "On disk: "), path),
      ),
    );
  }

  // ── layout ────────────────────────────────────────────────────────────────

  view.append(
    el(
      "div.sheet",
      null,
      el("p.eyebrow", null, "Stage 2 of 4"),
      el("h1", null, "Let the model name them"),
      el(
        "p.lede",
        null,
        "Sample frames and the source filename go to the provider you choose. It proposes a " +
          "description and a client for each clip. Anything it is unsure about is handed back to you.",
      ),

      messages,

      el(
        "section.panel",
        null,
        el("h2", null, "Provider"),
        el("p", null, "Keys stay on this machine. Only frames and filenames leave it."),
        el("div.field", null, el("label", { for: "provider" }, "Provider"), providerSelect),
        el("div", { style: { marginTop: "14px" } }, keySlot),
      ),

      el(
        "section.panel",
        null,
        el("h2", null, "How closely to look"),
        el("p", null, "More frames means better guesses and a larger bill."),
        el(
          "div",
          { style: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "14px" } },
          el("div.field", null, el("label", { for: "preset" }, "Detail"), presetSelect),
          el("div.field", null, el("label", { for: "model" }, "Model"), modelInput, modelOptions),
        ),
        estimate,
      ),

      guidePanel(),

      results,
    ),
  );

  function renderDock() {
    const c = counts();
    clear(dock).append(
      el("a.btn.ghost", { href: "#/prepare" }, "← Prepare"),
      el("span.tally", null, `${c.named} of ${c.total} named`),
      el("span.spacer"),
      runButton,
      el("a.btn.go", { href: "#/review" }, "Review names →"),
    );
  }

  renderDock();
  await refreshStatus();
}
