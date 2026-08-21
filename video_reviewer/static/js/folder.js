// Folder chooser. Tries the desktop picker first and falls back to browsing
// the filesystem in-app, because the native dialog is missing on plenty of
// Linux desktops.

import { api } from "./api.js";
import { el, clear } from "./dom.js";

/** Opens the in-app browser. Resolves to a path, or null if dismissed. */
export function browseFolder(startPath) {
  return new Promise((resolve) => {
    let here = null;

    const crumb = el("span.path");
    const dirs = el("div.dirs");
    const up = el("button.btn.ghost", { onclick: () => here && load(here.parent) }, "Up");
    const use = el("button.btn.primary", { onclick: () => finish(here && here.path) }, "Use this folder");

    const scrim = el(
      "div.scrim",
      { onclick: (event) => event.target === scrim && finish(null) },
      el(
        "div.picker",
        { role: "dialog", "aria-modal": "true", "aria-label": "Choose a folder" },
        el(
          "header",
          null,
          el("span", null, "Choose a folder"),
          el("button.btn.link", { onclick: () => finish(null) }, "Close"),
        ),
        el("div.crumb", null, up, crumb),
        dirs,
        el("footer", null, el("button.btn.ghost", { onclick: () => finish(null) }, "Cancel"), use),
      ),
    );

    function finish(path) {
      document.removeEventListener("keydown", onKey);
      scrim.remove();
      resolve(path || null);
    }

    function onKey(event) {
      if (event.key === "Escape") finish(null);
    }

    async function load(path) {
      clear(dirs).append(el("div.empty", null, "Reading folder…"));
      try {
        here = await api.browseDir(path);
      } catch (error) {
        clear(dirs).append(el("div.empty", null, error.message));
        return;
      }
      crumb.textContent = here.path;
      use.disabled = false;
      clear(dirs);
      if (!here.dirs.length) {
        dirs.append(el("div.empty", null, "No folders inside this one. You can still use it."));
        return;
      }
      for (const entry of here.dirs) {
        dirs.append(el("button", { onclick: () => load(entry.path), title: entry.path }, "▸", entry.name));
      }
    }

    document.addEventListener("keydown", onKey);
    document.body.append(scrim);
    use.disabled = true;
    load(startPath || "");
  });
}

/**
 * Ask for a folder the shortest way available: native dialog if the desktop
 * has one, otherwise the in-app browser. Resolves to a path or null.
 */
export async function chooseFolder(startPath) {
  try {
    const result = await api.pickDir();
    if (result.path) return result.path;
    if (result.cancelled) return null;
  } catch {
    // No native picker on this desktop — browse in-app instead.
  }
  return browseFolder(startPath);
}
