// Demo panel for the home page (only in demo mode): jog each belt by hand onto its square (Player 1 on Geppetto's Toy
// Workshop, Player 2 on Black Forest). The demo game is an ordinary saved game: "Continue game" opens its board, which
// plays the first moves on the belt.
import { h } from "/static/net.js";

const post = async (path) => {
  const r = await fetch(path, { method: "POST" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `${path} failed`);
  return data;
};

export async function mountDemoPanel() {
  const config = await fetch("/api/config").then(r => r.json()).catch(() => ({}));
  if (!config.demo) return;

  const players = config.jog_players?.length ? config.jog_players : [1];
  // Where each player is lined up by hand before Resume (demo): square ids from server/board.json.
  const START = { 1: { square: 5, name: "Geppetto's Toy Workshop" }, 2: { square: 1, name: "Black Forest" } };
  let direction = "forward", player = players[0];
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

  const q = (extra = "") => `?player=${player}${extra}`;
  const playerBtn = btn(`Jogging: Player ${player}`, () => {
    player = players[(players.indexOf(player) + 1) % players.length];
    playerBtn.textContent = `Jogging: Player ${player}`;
    homeBtn.textContent = `This is ${START[player].name}`;
  }, "secondary");
  const homeBtn = btn(`This is ${START[player].name}`, act(() => post(`/jog/home${q(`&square=${START[player].square}`)}`),
    "Belt tracked square set"));

  const jog = !config.jog ? h("p", { style: "margin:0;font-size:.85em" }, "Belt jog needs --lane-axes (or --virtual-axis).") : h("div", {},
    h("h4", { style: "margin:0 0 6px" }, "Motor setup"),
    players.length > 1 ? h("div", { style: "margin-bottom:6px" }, playerBtn) : null,
    h("div", { style: "display:flex;gap:6px;flex-wrap:wrap" },
      btn("Start", act(() => post(`/jog/start${q(`&direction=${direction}`)}`), "Running… press Stop when it's in place")),
      btn("Stop", act(() => post(`/jog/stop${q()}`), "Stopped"), "secondary"),
      dirBtn),
    h("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-top:6px" },
      btn("-50", act(() => post(`/jog/nudge${q("&steps=-50")}`), "Nudged back 50 steps"), "secondary"),
      btn("+50", act(() => post(`/jog/nudge${q("&steps=50")}`), "Nudged forward 50 steps"), "secondary"),
      homeBtn));

  const muse = h("input", { type: "checkbox", checked: !!config.muse });
  muse.onchange = async () => {
    say(muse.checked ? "Turning Muse on…" : "Turning Muse off…");
    try { await post(`/api/muse?on=${muse.checked}`); say(muse.checked ? "Muse is on" : "Muse is off"); }
    catch (e) { muse.checked = !muse.checked; say(e.message, true); }
  };

  document.body.append(h("aside", {
    style: "position:fixed;left:12px;bottom:12px;z-index:50;max-width:320px;padding:12px;border-radius:10px;" +
      "background:rgba(255,248,230,.96);color:#2b2118;box-shadow:0 4px 18px rgba(0,0,0,.3)" },
    h("h4", { style: "margin:0 0 6px" }, "Demo mode"), jog,
    h("label", { style: "display:flex;align-items:center;gap:6px;margin-top:8px" }, muse, "Muse AI"), status));
}
