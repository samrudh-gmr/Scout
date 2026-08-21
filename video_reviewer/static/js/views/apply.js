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

  const planHeading = el("h2");
  const planBody = el("div");
  const dockTally = el("span.tally");
  const dryButton = el("button.btn.ghost", { onclick: () => run(dryButton, true) }, "Check the plan");
  const applyButton = el("button.btn.go", { onclick: () => run(applyButton, false) }, "Rename files");

  // A successful apply/dry-run changes review_status (and therefore what's
  // "ready") without leaving this view, so the plan, heading, tally, and
  // buttons all need to be recomputed from the refreshed state afterward —
  // not just once at mount — or the operator keeps looking at a stale plan
  // for files that were just renamed.
  function refresh() {
    const c = counts();
    const ready = state.rows.filter((row) => ["approved", "applied"].includes(row.review_status));

    clear(planHeading).append(`Ready to rename — ${ready.length} of ${c.total}`);
    clear(planBody).append(
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
    );
    clear(dockTally).append(`${ready.length} approved · ${c.applied} already renamed`);
    dryButton.disabled = !ready.length;
    applyButton.disabled = !ready.length;
  }

  async function run(button, dryRun) {
    clear(messages);
    try {
      const result = await busy(button, dryRun ? "Checking…" : "Applying…", () =>
        api.apply({ output_dir: outputDir.value.trim() || null, dry_run: dryRun }),
      );
      await loadRows();
      refresh();
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

      el("section.panel", null, planHeading, planBody),

      output,
    ),
  );

  dock.append(
    el("a.btn.ghost", { href: "#/review" }, "← Review"),
    dockTally,
    el("span.spacer"),
    dryButton,
    applyButton,
  );

  refresh();
}
