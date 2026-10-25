// Demo mode, board page, bottom right: Muse on/off, reset the demo, and forward / back arrows for each player's token.
import { h } from "/static/net.js";

const post = async (path) => {
  const r = await fetch(path, { method: "POST" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : `${path} failed`);
  return data;
};

export async function mountDemoControls(gameId) {
  const [config, games] = await Promise.all([
    fetch("/api/config").then(r => r.json()).catch(() => ({})),
    fetch("/api/games").then(r => r.json()).catch(() => []),
  ]);
  if (!config.demo) return;
  const players = games.find(g => g.game_id === gameId)?.players ?? [{ id: "p1", name: "Player 1" }, { id: "p2", name: "Player 2" }];

  const status = h("div", { style: "font-size:.8em;min-height:1.1em;margin-top:6px" });
  const say = (text, bad) => { status.textContent = text; status.style.color = bad ? "#b3261e" : "inherit"; };
  const btn = (label, onclick, title = "") => h("button", { type: "button", class: "btn small secondary", title, onclick }, label);
  const step = (id, squares) => async () => {
    say("Moving…");
    try { const r = await post(`/api/demo/step?game=${gameId}&player=${id}&squares=${squares}`); say(`${id.toUpperCase()} is on square ${r.square}`); }
    catch (e) { say(e.message, true); }
  };

  const toggle = h("input", { type: "checkbox", checked: !!config.muse });
  toggle.onchange = async () => {
    say(toggle.checked ? "Turning Muse on…" : "Turning Muse off…");
    try { await post(`/api/muse?on=${toggle.checked}`); location.reload(); }
    catch (e) { toggle.checked = !toggle.checked; say(e.message, true); }
  };

  const reset = btn("Reset demo", async () => {
    if (!confirm("Drive both tokens back to their start squares and begin a fresh demo game?")) return;
    say("Resetting: driving the tokens back…");
    try { const { board_url } = await post(`/api/demo/reset?game=${gameId}`); location.href = board_url; }
    catch (e) { say(e.message, true); }
  }, "Tokens drive back to their lined-up squares and a fresh demo game opens");

  document.body.append(h("aside", {
    style: "position:fixed;right:12px;bottom:84px;z-index:45;padding:10px 12px;border-radius:10px;font-size:.9em;" +
      "background:rgba(255,248,230,.96);color:#2b2118;box-shadow:0 4px 18px rgba(0,0,0,.3)" },
    players.map(p => h("div", { style: "display:flex;align-items:center;gap:6px;margin-bottom:6px" },
      h("strong", { style: "min-width:84px" }, p.name),
      btn("◀", step(p.id, -1), `${p.name} back one square`), btn("▶", step(p.id, 1), `${p.name} forward one square`))),
    h("label", { style: "display:flex;align-items:center;gap:6px;margin:6px 0" }, toggle, "Muse AI"),
    reset, status));
}
