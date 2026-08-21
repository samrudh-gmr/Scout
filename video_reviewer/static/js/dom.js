// Tiny DOM helpers. No framework: the whole app is four views and a rail.

/**
 * el("div.panel", {onclick}, ...children) — tag with optional .classes and #id.
 * Children may be nodes, strings, or falsy (skipped).
 */
export function el(spec, props = null, ...children) {
  const [tagAndId, ...classes] = spec.split(".");
  const [tag, id] = tagAndId.split("#");
  const node = document.createElement(tag || "div");
  if (id) node.id = id;
  if (classes.length) node.className = classes.join(" ");

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = [node.className, value].filter(Boolean).join(" ");
    else if (key === "style") Object.assign(node.style, value);
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, value);
  }

  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export const $ = (selector, root = document) => root.querySelector(selector);

/**
 * append() that drops conditional children instead of printing "null".
 * Node.append stringifies them, so use this wherever a child is a ternary.
 */
export function add(parent, ...children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function pill(status) {
  const known = ["pending", "approved", "applied", "needs_review", "blocked", "working"];
  const cls = known.includes(status) ? status : "pending";
  return el(`span.pill.${cls}`, null, String(status || "pending").replace(/_/g, " "));
}

export function note(kind, message) {
  return el(`div.note.${kind}`, null, message);
}

/** A button that shows a spinner and disables itself while `work` runs. */
export function busy(button, label, work) {
  const original = button.textContent;
  button.disabled = true;
  clear(button).append(el("span.spin"), label);
  return Promise.resolve()
    .then(work)
    .finally(() => {
      button.disabled = false;
      clear(button).append(original);
    });
}

export function baseName(path) {
  return String(path || "").split("/").pop();
}
