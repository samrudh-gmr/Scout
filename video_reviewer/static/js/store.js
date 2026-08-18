// App state and the naming rule, shared by every view.

import { api } from "./api.js";

export const state = {
  rows: [],
  manifestPath: "",
  selected: 0,
  edits: {},        // index -> {description, client_or_location, year_month, sequence}
  provider: localStorage.getItem("provider") || "gemini",
  preset: localStorage.getItem("preset") || "balanced",
  inputDir: localStorage.getItem("inputDir") || "",
  outputDir: localStorage.getItem("outputDir") || "",
};

const listeners = new Set();

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emit() {
  for (const fn of listeners) fn();
}

export function remember(key, value) {
  state[key] = value;
  localStorage.setItem(key, value);
}

export async function loadRows() {
  const data = await api.rows();
  state.rows = data.rows || [];
  state.manifestPath = data.manifest_path || "";
  state.edits = {};
  if (state.selected >= state.rows.length) state.selected = 0;
  emit();
  return state.rows;
}

/** The row as it currently reads on screen: manifest values plus local edits. */
export function view(index) {
  const row = state.rows[index];
  if (!row) return null;
  return { ...row, ...(state.edits[index] || {}) };
}

export function edit(index, patch) {
  state.edits[index] = { ...(state.edits[index] || {}), ...patch };
  emit();
}

export const isEdited = (index) => Boolean(state.edits[index]);

export const dirty = () => Object.keys(state.edits).length;

// ── the naming rule ─────────────────────────────────────────────────────────
// Mirrors video_reviewer/sop.py so the assembled name updates as you type. The
// server still validates on save; this exists to show the operator the result.

const FORBIDDEN = /[/\\:*?"<>|]/;

export function checkSegment(name, value) {
  const clean = String(value || "").trim().replace(/\s+/g, " ");
  if (!clean) return { value: clean, error: `${name} is required` };
  if (clean.includes("_")) return { value: clean, error: `${name} cannot contain underscores` };
  if (FORBIDDEN.test(clean)) return { value: clean, error: `${name} has characters a filename can't hold` };
  return { value: clean, error: "" };
}

export function checkYearMonth(value) {
  const clean = String(value || "").trim();
  if (!clean) return { value: clean, error: "year-month is required" };
  if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(clean)) return { value: clean, error: "year-month must read YYYY-MM" };
  return { value: clean, error: "" };
}

export function checkSequence(value) {
  const clean = String(value || "").trim();
  if (!clean) return { value: clean, error: "sequence is required" };
  if (!/^\d+$/.test(clean)) return { value: clean, error: "sequence must be digits" };
  return { value: String(Number(clean)).padStart(3, "0"), error: "" };
}

/**
 * Break a row into the four SOP segments plus its extension, each carrying its
 * own error. Views render this directly — that is the name builder.
 */
export function assemble(row) {
  if (!row) return { segments: [], errors: ["No clip selected"], name: "" };
  const parts = [
    { key: "date", label: "year-month", ...checkYearMonth(row.year_month) },
    { key: "desc", label: "description", ...checkSegment("description", row.description) },
    { key: "client", label: "client / location", ...checkSegment("client or location", row.client_or_location) },
    { key: "seq", label: "sequence", ...checkSequence(row.sequence) },
  ];
  const ext = row.source_path_ext || "";
  const errors = parts.filter((p) => p.error).map((p) => p.error);
  const name = errors.length ? "" : parts.map((p) => p.value).join("_") + ext;
  return { segments: parts, ext, errors, name };
}

// ── pipeline stages ─────────────────────────────────────────────────────────

export const STAGES = [
  { id: "prepare", n: 1, label: "Prepare" },
  { id: "name", n: 2, label: "Name" },
  { id: "review", n: 3, label: "Review" },
  { id: "apply", n: 4, label: "Apply" },
];

export const counts = () => {
  const by = (status) => state.rows.filter((r) => r.review_status === status).length;
  return {
    total: state.rows.length,
    pending: by("pending"),
    approved: by("approved"),
    applied: by("applied"),
    needsReview: by("needs_review"),
    blocked: by("blocked"),
    named: state.rows.filter((r) => r.description && r.client_or_location).length,
  };
};

/** Which stages are behind you — drives the ticks in the rail. */
export function stageDone(id) {
  const c = counts();
  if (id === "prepare") return c.total > 0;
  if (id === "name") return c.total > 0 && c.named === c.total;
  if (id === "review") return c.total > 0 && c.approved + c.applied === c.total;
  if (id === "apply") return c.total > 0 && c.applied === c.total;
  return false;
}
