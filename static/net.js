// Tiny WebSocket helper shared by every page.
//
// The server sends:
//   {"type":"hello", ...}            once, right after connecting
//   {"type":"state","state":{...}}   the FULL game state after every change
//   events / {"type":"mirror"} / {"type":"error"}
// Pages redraw from "state" and treat everything else as extras.

// Close codes that mean "don't try again" (sent by server/main.py and rooms.py).
const FATAL = { 4001: "taken_over", 4400: "bad_request", 4403: "bad_link", 4404: "no_game", 4405: "in_person" };

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
      p.connected ? h("span", { class: "ok" }, "Connected") : h("span", { class: "wait" }, "Waiting"))));
}

export function kindLabel(kind) {
  return { in_person: "at the board", remote: "on a phone" }[kind] || kind;
}

export function tokenDot(p) {
  return h("span", { class: "token-dot", style: `background: var(--${p.id})` }, tokenGlyph(p));
}

export function tokenGlyph(p) {
  return (p.name.match(/[\p{L}\p{N}]/u)?.[0] || "?").toUpperCase();
}

// The Mirror's speaking voice: the best English voice this device has. Downloaded Premium /
// Enhanced voices first, then natural-sounding defaults; never a novelty voice (Albert, Zarvox, …).
// Voices load asynchronously, so call this at speak time rather than once at startup.
const VOICE_PREFS = ["Ava", "Zoe", "Evan", "Nathan", "Allison", "Susan", "Tom", "Serena", "Moira", "Daniel",
  "Samantha", "Karen", "Google UK English Female", "Google US English"];
export function narratorVoice() {
  if (!("speechSynthesis" in window)) return null;
  const en = speechSynthesis.getVoices().filter((v) => v.lang.startsWith("en"));
  const rank = (v) => {
    const q = /premium/i.test(v.name) ? 0 : /enhanced/i.test(v.name) ? 1 : 2;
    const i = VOICE_PREFS.findIndex((n) => v.name.startsWith(n));
    return q * 100 + (i < 0 ? 99 : i);
  };
  const best = en.sort((a, b) => rank(a) - rank(b))[0];
  return best && rank(best) % 100 < 99 ? best : null;   // nothing we know: let the browser pick its default
}

// Speak Mirror lines in order: ElevenLabs audio from the server (GET /tts) when it has a key,
// otherwise the browser's own voice. Each line's audio is fetched as soon as it is queued, so a
// line is usually ready by the time the one before it finishes.
const lines = [];
let current = null, playing = false;   // current: { audio, done } for the clip being played
export function narrate(text) {
  const clip = fetch(`/tts?text=${encodeURIComponent(text)}`)
    .then((r) => (r.ok ? r.blob() : null)).catch(() => null);
  lines.push({ text, clip });
  if (!playing) playNext();
}
export function stopNarration() {
  lines.length = 0;
  if (current) { current.audio.pause(); current.done(); }
  if ("speechSynthesis" in window) speechSynthesis.cancel();
}
async function playNext() {
  const line = lines.shift();
  if (!line) { playing = false; return; }
  playing = true;
  const blob = await line.clip;
  // The browser voice is only for lines the server has no ElevenLabs audio for (no key, or it failed).
  // A clip that will not play is skipped, never repeated in the browser voice.
  await (blob ? playBlob(blob) : speakBrowser(line.text)).catch((e) => console.warn("Mirror clip skipped:", e));
  playNext();
}
function playBlob(blob) {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);           // one element per clip: no events left over from the last one
  return new Promise((resolve, reject) => {
    const done = () => { URL.revokeObjectURL(url); current = null; resolve(); };
    current = { audio, done };
    audio.onended = done;
    audio.onerror = () => { URL.revokeObjectURL(url); current = null; reject(audio.error); };
    audio.play().catch((e) => { URL.revokeObjectURL(url); current = null; reject(e); });
  });
}
function speakBrowser(text) {
  if (!("speechSynthesis" in window)) return Promise.resolve();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 0.95; u.voice = narratorVoice();
  return new Promise((resolve) => { u.onend = u.onerror = resolve; speechSynthesis.speak(u); });
}
