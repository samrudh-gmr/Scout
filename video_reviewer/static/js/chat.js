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
 * @param {(patch: object) => void} onBatchFields proposes an all-clips update
 */
export function chatPanel({ onFields, onRemembered, onBatchFields }) {
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
  // What the model saw on the first turn. Holding it here is what lets every
  // later turn skip the images — the provider APIs remember nothing themselves.
  let frameNotes = "";

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
    // Snapshot which clip this request is actually for. If the operator
    // switches clips before the reply comes back, resetFor() below moves
    // `forIndex` and `messages` on to the new clip — comparing against the
    // snapshot (not the live `forIndex`) is what lets a stale reply be
    // dropped instead of landing on whatever clip is selected by then.
    const askedFor = forIndex;
    messages.push({ role: "user", content: text });
    busyNow = true;
    sendButton.disabled = true;
    redraw();

    try {
      const ask = (notes) =>
        api.aiChat({
          index: askedFor,
          messages: messages.map(({ role, content }) => ({ role, content })),
          provider: state.provider,
          frame_notes: notes,
        });

      let reply = await ask(frameNotes);
      if (reply.need_frames) {
        // The notes could not answer it — show the frames once more, then carry
        // on from the fresher notes. One retry only.
        reply = await ask("");
      }

      // The operator moved on to another clip while this was in flight — its
      // conversation was already reset, so applying this reply now would land
      // on the wrong clip (fields) and/or the wrong conversation (log).
      if (askedFor !== forIndex) return;

      if (reply.frame_notes) frameNotes = reply.frame_notes;

      const extras = [];
      const fields = reply.set_fields || {};
      if (Object.keys(fields).length) {
        onFields(fields);
        extras.push(
          el("div.applied", null, `Filled in ${Object.keys(fields).map(label).join(", ")} above.`),
        );
      }
      const batchFields = reply.set_batch_fields || {};
      if (Object.keys(batchFields).length) {
        onBatchFields(batchFields);
        extras.push(
          el("div.applied", null, `Proposed for every clip: ${Object.keys(batchFields).map(label).join(", ")}. Review and approve to save.`),
        );
      }
      if (reply.remembered) {
        onRemembered(reply.remembered);
        extras.push(el("div.applied.rule", null, `Added to the naming guide: ${reply.remembered}`));
      }
      messages.push({ role: "assistant", content: reply.message, extras });
    } catch (error) {
      if (askedFor === forIndex) messages.push({ role: "assistant", content: error.message, extras: [] });
    } finally {
      if (askedFor === forIndex) {
        busyNow = false;
        sendButton.disabled = false;
        redraw();
        input.focus();
      }
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
    el("h2", null, "Ask about this clip or batch"),
    log,
    el("div.chat-bar", null, input, sendButton),
  );

  panel.resetFor = (index) => {
    forIndex = index;
    messages = [];
    frameNotes = "";
    busyNow = false;
    redraw();
  };

  redraw();
  return panel;
}
