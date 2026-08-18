// Stage 3 — Review. The ingest bay: the shot log on the left, the clip in the
// middle, and the name being assembled on the right.

import { api } from "../api.js";
import { el, clear, add, pill, note, busy } from "../dom.js";
import { state, view as rowView, edit, assemble, loadRows, counts, isEdited } from "../store.js";
import { chatPanel } from "../chat.js";

export async function reviewView({ view, dock }) {
  const log = el("div.log");
  const stage = el("div.stage");
  const builder = el("div.builder");
  const chat = chatPanel({
    onFields: (fields) => {
      edit(state.selected, fields);
      renderBuilder();
      renderLog();
      renderDock();
    },
    onRemembered: (rule) => flash(note("ok", `Naming guide updated: ${rule}`)),
  });
  const side = el("div.side", null, builder, chat);
  const bay = el("div.bay", null, log, stage, side);
  view.append(bay);

  let showingFrame = -1; // -1 means the video itself

  // ── shot log ──────────────────────────────────────────────────────────────

  function renderLog() {
    const c = counts();
    clear(log).append(
      el(
        "div.log-head",
        null,
        el("h2", null, "Shot log"),
        el("span.count", null, `${c.approved + c.applied}/${c.total} done`),
      ),
    );

    state.rows.forEach((row, index) => {
      const current = rowView(index);
      const { name } = assemble(current);
      log.append(
        el(
          "button.clip",
          {
            "aria-selected": index === state.selected ? "true" : "false",
            onclick: () => select(index),
          },
          el("span.seq", null, String(index + 1).padStart(2, "0")),
          el(
            "span",
            null,
            el("span.src", { title: row.source_name }, row.source_name),
            el("span.out", null, name || "not named yet"),
            el(
              "span.tags",
              null,
              pill(isEdited(index) ? "needs_review" : row.review_status),
              row.ai_confidence ? el("span.conf", null, row.ai_confidence.toFixed(2)) : null,
            ),
          ),
        ),
      );
    });
  }

  function select(index) {
    state.selected = index;
    showingFrame = -1;
    renderLog();
    renderStage();
    renderBuilder();
    renderDock();
    chat.resetFor(index);   // a conversation is about one clip only
    log.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }

  // ── stage ─────────────────────────────────────────────────────────────────

  function renderStage() {
    const row = state.rows[state.selected];
    clear(stage);
    if (!row) return;

    if (showingFrame >= 0) {
      stage.append(
        el("img.player", {
          src: api.frameUrl(state.selected, showingFrame),
          alt: `Frame ${showingFrame + 1} of ${row.source_name}`,
          style: { objectFit: "contain" },
        }),
      );
    } else {
      const player = el("video.player", {
        src: api.videoUrl(state.selected),
        controls: true,
        preload: "metadata",
      });
      // No proxy on disk is normal for formats ffmpeg could not transcode; the
      // sample frames below still carry the content.
      player.addEventListener("error", () => {
        player.replaceWith(
          el(
            "div.player.player-empty",
            null,
            el("span", null, "No preview copy for this clip"),
            el("span", { style: { fontSize: "12px" } }, "The frames below still show what is in it."),
          ),
        );
      });
      stage.append(player);
    }

    const strip = el("div.strip");
    for (let i = 0; i < row.frame_count; i += 1) {
      strip.append(
        el(
          "button",
          {
            "aria-pressed": i === showingFrame ? "true" : "false",
            title: `Frame ${i + 1}`,
            onclick: () => {
              showingFrame = showingFrame === i ? -1 : i;
              renderStage();
            },
          },
          el("img", { src: api.frameUrl(state.selected, i), alt: `Frame ${i + 1}`, loading: "lazy" }),
        ),
      );
    }
    if (row.frame_count) stage.append(strip);

    stage.append(
      el("div.source-line", null, el("span.k", null, "Source"), el("span.v", null, row.source_name)),
    );
  }

  // ── name builder ──────────────────────────────────────────────────────────

  function renderBuilder() {
    const index = state.selected;
    const row = rowView(index);
    clear(builder);
    if (!row) return;

    const { ext, errors } = assemble(row);
    const nameLine = el("div.name");

    function paintName() {
      const live = assemble(rowView(index));
      clear(nameLine);
      live.segments.forEach((segment, i) => {
        if (i) nameLine.append(el("span.u", null, "_"));
        nameLine.append(
          segment.error
            ? el("span.missing", { "data-seg": segment.key }, `⟨${segment.label}⟩`)
            : el(`span.s.${segment.key}`, { "data-seg": segment.key }, segment.value),
        );
      });
      nameLine.append(el("span.s.ext", null, ext));
    }

    const fields = [
      { key: "date", field: "year_month", label: "Year-month", placeholder: "2024-07" },
      { key: "desc", field: "description", label: "Description", placeholder: "Robotic Sanding Composite Panel" },
      { key: "client", field: "client_or_location", label: "Client / location", placeholder: "SOLV California" },
      { key: "seq", field: "sequence", label: "Sequence", placeholder: "001" },
    ];

    const segs = el("div.segs");
    for (const spec of fields) {
      const input = el("input", {
        type: "text",
        id: `seg-${spec.key}`,
        value: row[spec.field] ?? "",
        placeholder: spec.placeholder,
        oninput: (event) => {
          edit(index, { [spec.field]: event.target.value });
          paintName();
          renderLog();
          renderDock();
        },
        // Focusing a field lights up the slice of the filename it feeds.
        onfocus: () => nameLine.querySelector(`[data-seg="${spec.key}"]`)?.classList.add("lit"),
        onblur: () => nameLine.querySelectorAll(".lit").forEach((node) => node.classList.remove("lit")),
      });
      segs.append(
        el(`div.seg.${spec.key}`, null, el("label", { for: `seg-${spec.key}` }, spec.label), input),
      );
    }

    add(
      builder,
      el("h2", null, "Name"),
      segs,
      el(
        "div.assembly",
        null,
        el("span.cap", null, "Will be saved as"),
        nameLine,
      ),
      errors.length ? note("warn", errors[0]) : null,
      row.ai_rationale
        ? el(
            "div.rationale",
            null,
            el("span.who", null, `Model note · confidence ${(row.ai_confidence || 0).toFixed(2)}`),
            row.ai_rationale,
          )
        : null,
      row.ai_flags ? el("div.rationale", null, el("span.who", null, "Flags"), row.ai_flags) : null,
    );

    paintName();
  }

  // ── saving ────────────────────────────────────────────────────────────────

  async function approve(indices, button) {
    const payload = indices
      .map((index) => {
        const row = rowView(index);
        const { errors } = assemble(row);
        return errors.length ? null : {
          index,
          checked: true,
          description: row.description,
          client_or_location: row.client_or_location,
          year_month: row.year_month,
          sequence: row.sequence,
        };
      })
      .filter(Boolean);

    if (!payload.length) {
      flash(note("err", "Fill in every part of the name before approving."));
      return false;
    }

    try {
      await busy(button, "Saving…", () => api.save(payload));
      await loadRows();
      renderLog();
      renderBuilder();
      renderDock();
      return true;
    } catch (error) {
      flash(note("err", error.message));
      return false;
    }
  }

  const toasts = el("div.toasts");
  view.append(toasts);

  function flash(node) {
    // Outside the panels, which re-render on save and would wipe the message.
    toasts.append(node);
    setTimeout(() => node.remove(), 4500);
  }

  // Wraps around: clips left behind earlier in the log still need doing.
  function nextUnapproved(from) {
    const total = state.rows.length;
    for (let step = 1; step <= total; step += 1) {
      const i = (from + step) % total;
      if (!["approved", "applied"].includes(state.rows[i].review_status)) return i;
    }
    return -1;
  }

  // ── dock ──────────────────────────────────────────────────────────────────

  function renderDock() {
    const c = counts();
    const row = rowView(state.selected);
    const ready = row && !assemble(row).errors.length;
    const remaining = state.rows.filter((r) => !["approved", "applied"].includes(r.review_status));
    // Only complete rows can be approved; incomplete ones still need your eyes.
    const unapproved = state.rows.filter(
      (r, i) => !["approved", "applied"].includes(r.review_status) && !assemble(rowView(i)).errors.length,
    );

    const approveButton = el(
      "button.btn.go",
      {
        disabled: !ready,
        onclick: async () => {
          const here = state.selected;
          const name = assemble(rowView(here)).name;
          if (await approve([here], approveButton)) {
            const next = nextUnapproved(here);
            if (next >= 0) {
              select(next);
              flash(note("ok", `Approved ${name}`));
            } else {
              flash(note("ok", `Approved ${name} — that was the last one. Go to Apply.`));
            }
          }
        },
      },
      ready ? "Approve & next" : "Fill in the name first",
    );

    const approveAllButton = el(
      "button.btn.ghost",
      {
        disabled: !unapproved.length,
        onclick: async () => {
          const all = state.rows
            .map((_, i) => i)
            .filter((i) => !assemble(rowView(i)).errors.length);
          const before = counts().approved + counts().applied;
          if (await approve(all, approveAllButton)) {
            const gained = counts().approved + counts().applied - before;
            flash(note("ok", gained
              ? `Approved ${gained} clip${gained === 1 ? "" : "s"}.`
              : "Those were already approved — nothing changed."));
          }
        },
      },
      unapproved.length
        ? `Approve all complete (${unapproved.length} left)`
        : remaining.length
          ? "Nothing else is complete"
          : "All clips approved",
    );

    clear(dock).append(
      el("a.btn.ghost", { href: "#/name" }, "← Name"),
      el("span.tally", null, `${c.approved + c.applied} of ${c.total} approved`),
      el("span.spacer"),
      approveAllButton,
      approveButton,
      el("a.btn.primary", { href: "#/apply" }, "Apply →"),
    );
  }

  // ── keyboard ──────────────────────────────────────────────────────────────

  function onKey(event) {
    if (event.target.matches("input, select, textarea")) return;
    if (event.key === "ArrowDown" || event.key === "j") {
      event.preventDefault();
      select(Math.min(state.selected + 1, state.rows.length - 1));
    } else if (event.key === "ArrowUp" || event.key === "k") {
      event.preventDefault();
      select(Math.max(state.selected - 1, 0));
    }
  }

  document.addEventListener("keydown", onKey);

  select(state.selected);
  return () => document.removeEventListener("keydown", onKey);
}
