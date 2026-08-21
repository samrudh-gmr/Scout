// Shell: theme, the pipeline rail, and hash routing between the four stages.

import { el, clear, $ } from "./dom.js";
import { api } from "./api.js";
import { state, subscribe, loadRows, STAGES, stageDone, counts } from "./store.js";
import { prepareView } from "./views/prepare.js";
import { nameView } from "./views/name.js";
import { reviewView } from "./views/review.js";
import { applyView } from "./views/apply.js";

const VIEWS = { prepare: prepareView, name: nameView, review: reviewView, apply: applyView };

// ── theme ───────────────────────────────────────────────────────────────────

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("theme", theme);
}

applyTheme(localStorage.getItem("theme") || "dark");

$("#theme").addEventListener("click", () => {
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
});

function renderAppInfo(info) {
  const version = $("#version");
  const update = $("#update");
  version.textContent = info.current_version ? `v${info.current_version}` : "";
  if (!info.update_available || !info.release_url) return;
  update.hidden = false;
  update.textContent = `Update to v${info.latest_version}`;
  update.title = "Open the latest Scout release installer";
  update.addEventListener("click", () => window.open(info.release_url, "_blank", "noopener"), { once: true });
}

// ── rail ────────────────────────────────────────────────────────────────────

function renderRail(current) {
  const rail = clear($("#rail"));
  STAGES.forEach((stage, i) => {
    if (i) rail.append(el("span.tick"));
    const done = stageDone(stage.id);
    rail.append(
      el(
        "a",
        {
          href: `#/${stage.id}`,
          class: done ? "done" : "",
          "aria-current": stage.id === current ? "page" : null,
        },
        el("span.n", null, done && stage.id !== current ? "✓" : stage.n),
        el("span.lbl", null, stage.label),
      ),
    );
  });

  const manifest = $("#manifest");
  manifest.textContent = state.manifestPath;
  manifest.title = state.manifestPath;
}

// ── routing ─────────────────────────────────────────────────────────────────

export function go(stage) {
  window.location.hash = `#/${stage}`;
}

function currentStage() {
  const id = (window.location.hash || "").replace(/^#\/?/, "").split("?")[0];
  return VIEWS[id] ? id : "prepare";
}

let teardown = null;

async function render() {
  const stage = currentStage();
  renderRail(stage);

  if (teardown) teardown();
  const view = clear($("#view"));
  const dock = clear($("#dock"));

  // A stage you can't act on yet sends you back to the one that unblocks it.
  if (stage !== "prepare" && !counts().total) {
    view.append(
      el(
        "div.empty-state",
        null,
        el("p", null, "No clips are loaded yet. Start by preparing a folder of video files."),
        el("a.btn.primary", { href: "#/prepare" }, "Go to Prepare"),
      ),
    );
    return;
  }

  teardown = (await VIEWS[stage]({ view, dock, go })) || null;
}

window.addEventListener("hashchange", render);
subscribe(() => renderRail(currentStage()));

// ── boot ────────────────────────────────────────────────────────────────────

(async function start() {
  api.appInfo().then(renderAppInfo).catch(() => {});
  try {
    await loadRows();
  } catch (error) {
    $("#view").append(el("div.empty-state", null, el("p", null, error.message)));
    return;
  }
  if (!window.location.hash) {
    // Pick up where the batch actually is rather than always opening at step 1.
    const c = counts();
    const landing = !c.total
      ? "prepare"
      : c.named < c.total
        ? "name"
        : c.approved + c.applied < c.total
          ? "review"
          : "apply";
    // replaceState, not hash assignment: this is the entry point, not a step
    // the operator took, so it should not sit in their back history.
    history.replaceState(null, "", `#/${landing}`);
  }
  render();
})();
