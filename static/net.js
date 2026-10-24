// Tiny WebSocket helper shared by every page.
//
// The server sends:
//   {"type":"hello", ...}            once, right after connecting
//   {"type":"state","state":{...}}   the FULL game state after every change
//   events / {"type":"mirror"} / {"type":"error"}
// Pages redraw from "state" and treat everything else as extras.

// Close codes that mean "don't try again" (sent by server/main.py and rooms.py).
const FATAL = { 4001: "taken_over", 4400: "bad_request", 4403: "bad_link", 4404: "no_game" };

export function connect(path, { onHello, onState, onMessage, onError, onClosed, onStatus } = {}) {
  let ws = null;
  let delay = 1000;
  let lastFatalMessage = null;
  let stopped = false;

  function open() {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${scheme}://${location.host}${path}`);
    onStatus?.("connecting");

    ws.onopen = () => { delay = 1000; onStatus?.("online"); };

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "hello") onHello?.(msg);
      else if (msg.type === "state") onState?.(msg.state);
      else if (msg.type === "error") {
        if (msg.fatal || /taken over/.test(msg.message)) lastFatalMessage = msg.message;
        else onError?.(msg.message);
      }
      if (msg.type !== "state") onMessage?.(msg);
    };

    ws.onclose = (e) => {
      if (stopped) return;
      if (FATAL[e.code]) {
        stopped = true;
        onStatus?.("closed");
        onClosed?.(FATAL[e.code], lastFatalMessage);
        return;
      }
      // Dropped (Wi-Fi blip, server restart): try again, backing off up to 5 s.
      onStatus?.("offline");
      setTimeout(open, delay);
      delay = Math.min(delay * 2, 5000);
    };
  }

  open();

  return {
    send(obj) {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
      else onError?.("reconnecting… try again in a moment");
    },
  };
}

// Small shared UI helpers.

export function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

// Build an element: h("li", {class: "x"}, "text", childEl)
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  el.append(...children.flat().filter((c) => c != null && c !== false));
  return el;
}

export function seatList(state) {
  return h("ul", { class: "seats" }, state.players.map((p) =>
    h("li", {},
      tokenDot(p),
      h("span", { class: "name" }, p.name, " ", h("span", { class: "muted" }, kindLabel(p.kind))),
      p.connected ? h("span", { class: "ok" }, "✓ here") : h("span", { class: "wait" }, "⏳ waiting"))));
}

export function kindLabel(kind) {
  return { in_person: "at the board", remote: "on a phone" }[kind] || kind;
}

export function tokenDot(p) {
  return h("span", { class: "token-dot", style: `background: var(--${p.id})` }, tokenGlyph(p));
}

export function tokenGlyph(p) {
  if (p.kind === "in_person") return "👵";
  return (p.name[0] || "?").toUpperCase();
}
