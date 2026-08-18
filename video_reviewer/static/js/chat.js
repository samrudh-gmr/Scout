// The ask panel in Review: a conversation about the clip you are looking at.
//
// Anything the model proposes lands in the name fields as an ordinary edit, so
// it shows up in the assembled filename and still waits for Approve. The chat
// never writes the manifest.

import { api } from "./api.js";
import { el, clear, add } from "./dom.js";
import { state, edit } from "./store.js";

const OPENERS = [
  "What is happening in this clip?",
  "Is the client name right?",
  "Suggest a better description",
];

/**
 * @param {(patch: object) => void} onFields  applies proposed fields to the form
 * @param {(text: string) => void} onRemembered  reports a rule added to the guide
 */
export function chatPanel({ onFields, onRemembered }) {
  const log = el("div.chat-log");
  const input = el("input", {
    type: "text",
    id: "chatInput",
    placeholder: "Ask about this clip…",
    autocomplete: "off",
    onkeydown: (event) => event.key === "Enter" && send(),
  });
  const sendButton = el("button.btn.primary", { onclick: () => send() }, "Ask");

  // One conversation per clip: switching clips starts a fresh one.
  let messages = [];
  let forIndex = state.selected;
  let busyNow = false;

  function bubble(role, text, extras = []) {
    return add(el(`div.msg.${role}`, null, el("div.who", null, role === "user" ? "You" : "Assistant"),
      el("div.body", null, text)), ...extras);
  }

  function scroll() {
    log.scrollTop = log.scrollHeight;
  }

  function renderOpeners() {
    if (messages.length) return null;
    return el(
      "div.openers",
      null,
      OPENERS.map((text) =>
        el("button.chip", { onclick: () => { input.value = text; send(); } }, text),
      ),
    );
  }

  function redraw() {
    clear(log);
    if (!messages.length) {
      log.append(
        el("p.chat-empty", null, "Ask about the clip on screen. Suggestions land in the fields above for you to approve."),
        renderOpeners(),
      );
    }
    for (const message of messages) {
      log.append(bubble(message.role, message.content, message.extras || []));
    }
    if (busyNow) log.append(el("div.msg.assistant", null, el("div.who", null, "Assistant"), el("div.body", null, el("span.spin"), " thinking…")));
    scroll();
  }

  async function send() {
    const text = input.value.trim();
    if (!text || busyNow) return;
    input.value = "";
    messages.push({ role: "user", content: text });
    busyNow = true;
    sendButton.disabled = true;
    redraw();

    try {
      const reply = await api.aiChat({
        index: forIndex,
        messages: messages.map(({ role, content }) => ({ role, content })),
        provider: state.provider,
      });

      const extras = [];
      const fields = reply.set_fields || {};
      if (Object.keys(fields).length) {
        onFields(fields);
        extras.push(
          el("div.applied", null, `Filled in ${Object.keys(fields).map(label).join(", ")} above.`),
        );
      }
      if (reply.remembered) {
        onRemembered(reply.remembered);
        extras.push(el("div.applied.rule", null, `Added to the naming guide: ${reply.remembered}`));
      }
      messages.push({ role: "assistant", content: reply.message, extras });
    } catch (error) {
      messages.push({ role: "assistant", content: error.message, extras: [] });
    } finally {
      busyNow = false;
      sendButton.disabled = false;
      redraw();
      input.focus();
    }
  }

  function label(field) {
    return {
      description: "description",
      client_or_location: "client",
      year_month: "year-month",
      sequence: "sequence",
    }[field] || field;
  }

  const panel = el(
    "section.chat",
    null,
    el("h2", null, "Ask about this clip"),
    log,
    el("div.chat-bar", null, input, sendButton),
  );

  panel.resetFor = (index) => {
    forIndex = index;
    messages = [];
    busyNow = false;
    redraw();
  };

  redraw();
  return panel;
}
