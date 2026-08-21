// Every call the app makes to the local server, in one place.
// Each function either returns parsed JSON or throws an Error whose message is
// already fit to show a person — views never have to invent wording.

const BASE = "/api";

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(BASE + path, options);
  } catch {
    throw new Error("The local app stopped responding. Check the terminal window it runs in.");
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function post(path, body) {
  return request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export const api = {
  rows: () => request("/rows"),

  appInfo: () => request("/app-info"),

  save: (rows) => post("/save", { rows }),

  prepare: (body) => post("/run-prepare", body),

  apply: (body) => post("/run-apply", body),

  aiStatus: (provider) => request(`/ai/status?provider=${encodeURIComponent(provider)}`),

  aiReview: (body) => post("/ai/review", body),

  aiChat: (body) => post("/ai/chat", body),

  saveKey: (provider, apiKey) => post("/settings/key", { provider, api_key: apiKey }),

  forgetKey: (provider) =>
    request(`/settings/key?provider=${encodeURIComponent(provider)}`, { method: "DELETE" }),

  namingGuide: () => request("/naming-guide"),

  saveNamingGuide: (text) => post("/naming-guide", { text }),

  resetNamingGuide: () => post("/naming-guide/reset", {}),

  browseDir: (path) => request(`/browse-dir?path=${encodeURIComponent(path || "")}`),

  pickDir: () => request("/pick-dir"),

  videoUrl: (index) => `${BASE}/video/${index}`,

  frameUrl: (rowIndex, frameIndex) => `${BASE}/frame/${rowIndex}/${frameIndex}`,
};
