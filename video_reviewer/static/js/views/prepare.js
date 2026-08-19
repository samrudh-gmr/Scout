// Stage 1 — Prepare. Point at a folder; the app builds proxies and sample
// frames for every video it finds, and writes the batch manifest.

import { api } from "../api.js";
import { el, clear, add, busy, note } from "../dom.js";
import { state, remember, loadRows, counts } from "../store.js";
import { chooseFolder, browseFolder } from "../folder.js";

export async function prepareView({ view, dock, go }) {
  const messages = el("div");

  const folder = el("input", {
    type: "text",
    id: "inputDir",
    value: state.inputDir,
    placeholder: "/path/to/the/camera/folder",
    oninput: (event) => remember("inputDir", event.target.value),
  });

  const yearMonth = el("input", {
    type: "text",
    id: "yearMonth",
    placeholder: "2024-07",
    maxlength: 7,
  });

  const client = el("input", {
    type: "text",
    id: "batchClient",
    placeholder: "Optional — e.g. SOLV California",
  });

  const setFolder = (path) => {
    if (!path) return;
    folder.value = path;
    remember("inputDir", path);
  };

  const runPrepare = (button) =>
    busy(button, "Preparing…", async () => {
      const input = folder.value.trim();
      clear(messages);
      if (!input) {
        messages.append(note("err", "Choose a folder of video files first."));
        return;
      }
      try {
        const result = await api.prepare({
          input_dir: input,
          year_month: yearMonth.value.trim(),
          client_or_location: client.value.trim(),
          tmp_dir: `${input.replace(/\/$/, "")}/.video-renamer-tmp`,
          sample_count: 0,
          proxy_scale: 1280,
        });
        await loadRows();
        clear(messages).append(note("ok", result.message || "Folder prepared."));
        go("name");
      } catch (error) {
        messages.append(note("err", error.message));
      }
    });

  const c = counts();

  view.append(
    el(
      "div.sheet",
      null,
      el("p.eyebrow", null, "Stage 1 of 4"),
      el("h1", null, "Prepare the batch"),
      el(
        "p.lede",
        null,
        "Pick the folder your camera exports landed in. Every video inside gets a lightweight " +
          "preview copy and a set of sample frames, so naming and review stay fast on large files.",
      ),

      messages,

      el(
        "section.panel",
        null,
        el("h2", null, "Video folder"),
        el("p", null, "Nothing is moved or renamed at this stage."),
        el(
          "div.row-2",
          null,
          el("div.field", null, el("label", { for: "inputDir" }, "Folder"), folder),
          el(
            "div",
            { style: { display: "flex", gap: "8px" } },
            el(
              "button.btn.ghost",
              { onclick: async () => setFolder(await chooseFolder(folder.value)) },
              "Choose…",
            ),
            el(
              "button.btn.ghost",
              { onclick: async () => setFolder(await browseFolder(folder.value)) },
              "Browse",
            ),
          ),
        ),
        el(
          "div.field",
          { style: { marginTop: "14px", maxWidth: "220px" } },
          el("label", { for: "yearMonth" }, "Year-month override"),
          yearMonth,
        ),
        el(
          "div.field",
          { style: { marginTop: "14px", maxWidth: "420px" } },
          el("label", { for: "batchClient" }, "Client / location (optional)"),
          client,
        ),
        el(
          "p.hint",
          null,
          "Leave the date blank to read it from each filename. Set a date or client here when it applies to every video in this folder.",
        ),
      ),

      c.total
        ? el(
            "section.panel",
            null,
            el("h2", null, "Current batch"),
            el(
              "p",
              null,
              `${c.total} clip${c.total === 1 ? "" : "s"} are already loaded. Preparing a folder ` +
                "replaces them — or keep them and carry on from the bar below.",
            ),
          )
        : null,
    ),
  );

  const prepareButton = el("button.btn.primary", { onclick: () => runPrepare(prepareButton) }, "Prepare videos");
  add(
    dock,
    el("span.tally", null, c.total ? `${c.total} clip${c.total === 1 ? "" : "s"} in the batch` : "No batch yet"),
    el("span.spacer"),
    c.total ? el("a.btn.ghost", { href: "#/name" }, "Keep these and continue →") : null,
    prepareButton,
  );
}
