// Demo mode, board page: the phone invite link (and QR code) of each remote player, so a phone can join a resumed game.
import { h } from "/static/net.js";

export async function mountInvites(gameId) {
  const { players = [] } = await fetch(`/api/games/${gameId}/invites`).then(r => r.ok ? r.json() : {}).catch(() => ({}));
  if (!players.length) return;
  const rows = players.map(p => {
    const qr = h("div", { style: "margin-top:6px" });
    try { new QRCode(qr, { text: p.url, width: 112, height: 112, correctLevel: QRCode.CorrectLevel.M }); } catch {}
    const copy = h("button", { type: "button", class: "btn small secondary" }, "Copy link");
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(p.url); copy.textContent = "Copied"; } catch { copy.textContent = "Copy failed"; }
      setTimeout(() => { copy.textContent = "Copy link"; }, 1500);
    };
    return h("div", { style: "margin-top:8px" },
      h("strong", {}, `${p.name} — invite link`), h("div", { style: "font-size:.8em;word-break:break-all" }, p.url), copy, qr);
  });
  document.body.append(h("details", {
    style: "position:fixed;left:12px;bottom:12px;z-index:40;max-width:280px;padding:8px 12px;border-radius:10px;" +
      "background:rgba(255,248,230,.96);color:#2b2118;box-shadow:0 4px 18px rgba(0,0,0,.3)", open: true },
    h("summary", { style: "cursor:pointer;font-weight:700" }, "Invite a player"), rows));
}
