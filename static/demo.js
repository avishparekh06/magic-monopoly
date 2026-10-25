// Demo panel for the home page (only with --dev): jog the belt by hand to GO, then "Resume (demo)" drives the
// token to where the most recent saved game has it and opens that game's board.
import { h } from "/static/net.js";

const post = async (path) => {
  const r = await fetch(path, { method: "POST" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `${path} failed`);
  return data;
};

export async function mountDemoPanel() {
  const config = await fetch("/api/config").then(r => r.json()).catch(() => ({}));
  if (!config.dev) return;

  let direction = "forward";
  const status = h("p", { style: "margin:6px 0 0;font-size:.85em;min-height:1.2em" });
  const say = (text, bad) => { status.textContent = text; status.style.color = bad ? "#b3261e" : "inherit"; };
  const act = (fn, done) => async () => {
    try { await fn(); say(done); } catch (e) { say(e.message, true); }
  };
  const btn = (label, onclick, extra = "") => h("button", { type: "button", class: `btn small ${extra}`, onclick }, label);

  const dirBtn = btn("Direction: forward", () => {
    direction = direction === "forward" ? "back" : "forward";
    dirBtn.textContent = `Direction: ${direction}`;
  }, "secondary");

  const jog = !config.jog ? h("p", { style: "margin:0;font-size:.85em" }, "Belt jog needs --bridge arduino.") : h("div", {},
    h("h4", { style: "margin:0 0 6px" }, "Motor setup"),
    h("div", { style: "display:flex;gap:6px;flex-wrap:wrap" },
      btn("Start", act(() => post(`/jog/start?direction=${direction}`), "Running… press Stop when it's in place")),
      btn("Stop", act(() => post("/jog/stop"), "Stopped"), "secondary"),
      dirBtn),
    h("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-top:6px" },
      btn("-50", act(() => post("/jog/nudge?steps=-50"), "Nudged back 50 steps"), "secondary"),
      btn("+50", act(() => post("/jog/nudge?steps=50"), "Nudged forward 50 steps"), "secondary"),
      btn("This is GO", act(() => post("/jog/home"), "Token is now GO (square 0)"))));

  const resume = btn("Resume (demo)", act(async () => {
    const games = await fetch("/api/games").then(r => r.json());
    const game = games.find(g => !["over", "abandoned"].includes(g.status) && g.phase !== "LOBBY" && g.live);
    if (!game) throw new Error("No saved game in progress to resume");
    say("Driving the token to its square…");
    const { board_url } = await post(`/api/games/${game.game_id}/demo-resume`);
    location.href = board_url;
  }, ""), "wood");

  document.body.append(h("aside", {
    style: "position:fixed;left:12px;bottom:12px;z-index:50;max-width:320px;padding:12px;border-radius:10px;" +
      "background:rgba(255,248,230,.96);color:#2b2118;box-shadow:0 4px 18px rgba(0,0,0,.3)" },
    h("h4", { style: "margin:0 0 6px" }, "Demo mode"), jog,
    h("div", { style: "margin-top:10px" }, resume), status));
}
