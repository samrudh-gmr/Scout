// Stage 4 — Apply. Show exactly what will happen, then do it. Renaming in
// place is the default; naming an output folder copies instead, leaving the
// camera files untouched.

import { api } from "../api.js";
import { el, clear, note, busy } from "../dom.js";
import { state, remember, loadRows, counts } from "../store.js";
import { chooseFolder, browseFolder } from "../folder.js";

export async function applyView({ view, dock }) {
  const messages = el("div");
  const output = el("div");

  const outputDir = el("input", {
    type: "text",
    id: "outputDir",
    value: state.outputDir,
    placeholder: "leave blank to rename the files where they are",
    oninput: (event) => remember("outputDir", event.target.value),
  });

  const setOutput = (path) => {
    if (!path) return;
    outputDir.value = path;
    remember("outputDir", path);
  };

  const c = counts();
  const ready = state.rows.filter((row) => ["approved", "applied"].includes(row.review_status));

  async function run(button, dryRun) {
    clear(messages);
    try {
      const result = await busy(button, dryRun ? "Checking…" : "Applying…", () =>
        api.apply({ output_dir: outputDir.value.trim() || null, dry_run: dryRun }),
      );
      await loadRows();
      clear(output).append(el("pre.log-out", null, result.output || "Nothing to report."));
      messages.append(
        result.ok
          ? note("ok", dryRun ? "The plan above is what will happen." : "Files renamed.")
          : note("err", "Some files could not be handled. The report above says which."),
      );
    } catch (error) {
      messages.append(note("err", error.message));
    }
  }

  view.append(
    el(
      "div.sheet",
      null,
      el("p.eyebrow", null, "Stage 4 of 4"),
      el("h1", null, "Apply the names"),
      el(
        "p.lede",
        null,
        "Only approved clips are touched. Check the plan first — it lists every file and the name " +
          "it will take.",
      ),

      messages,

      el(
        "section.panel",
        null,
        el("h2", null, "Where the renamed files go"),
        el("p", null, "Blank renames each file in place. A folder copies them there instead."),
        el(
          "div.row-2",
          null,
          el("div.field", null, el("label", { for: "outputDir" }, "Output folder"), outputDir),
          el(
            "div",
            { style: { display: "flex", gap: "8px" } },
            el("button.btn.ghost", { onclick: async () => setOutput(await chooseFolder(outputDir.value)) }, "Choose…"),
            el("button.btn.ghost", { onclick: async () => setOutput(await browseFolder(outputDir.value)) }, "Browse"),
          ),
        ),
      ),

      el(
        "section.panel",
        null,
        el("h2", null, `Ready to rename — ${ready.length} of ${c.total}`),
        el("p", null, ready.length ? "Approved in Review." : "Approve clips in Review before applying."),
        ready.length
          ? el(
              "div.plan",
              null,
              ready.map((row) =>
                el(
                  "div.r",
                  null,
                  el("span.from", { title: row.source_name }, row.source_name),
                  el("span.arrow", null, "→"),
                  el("span.to", null, row.proposed_name),
                ),
              ),
            )
          : null,
      ),

      output,
    ),
  );

  const dryButton = el("button.btn.ghost", { onclick: () => run(dryButton, true), disabled: !ready.length }, "Check the plan");
  const applyButton = el("button.btn.go", { onclick: () => run(applyButton, false), disabled: !ready.length }, "Rename files");

  dock.append(
    el("a.btn.ghost", { href: "#/review" }, "← Review"),
    el("span.tally", null, `${ready.length} approved · ${c.applied} already renamed`),
    el("span.spacer"),
    dryButton,
    applyButton,
  );
}
