// Shared 2D board renderer, used by the board laptop page and the kid app.
//
//   const board = createBoardView(containerEl, await loadBoard());
//   board.render(state);        // call on every state message
//   board.center                // the empty middle; pages put their own content here
//
// Layout: an 11x11 CSS grid where only the outer ring holds squares.
// Square 0 (Castle Gate) is bottom-right; play runs left along the bottom,
// up the left side, right along the top and down the right side.

import { tokenGlyph } from "/static/net.js";

const ICONS = {
  0: "🏰", 2: "🧚", 4: "👑", 5: "🧞", 7: "✨", 10: "🗼", 12: "⛲", 15: "🎃",
  17: "🧚", 20: "🍄", 22: "✨", 25: "👢", 28: "🧶", 30: "🌀", 33: "🧚",
  35: "🧹", 36: "✨", 38: "🐲",
};
const STEP_MS = 190;       // time per square when a token walks...
const MAX_WALK_MS = 3000;  // ...but long card moves (up to 39 squares) speed up to fit in ~3 s

export async function loadBoard() {
  return (await fetch("/api/board")).json();
}

// Grid row/column (1-based) and which side of the board a square is on.
function cellPos(i) {
  if (i <= 10) return { row: 11, col: 11 - i, side: "b" };
  if (i <= 20) return { row: 21 - i, col: 1, side: "l" };
  if (i <= 30) return { row: 1, col: i - 19, side: "t" };
  return { row: i - 29, col: 11, side: "r" };
}

export function createBoardView(el, boardData) {
  injectStyles();
  el.classList.add("mm-board");
  el.replaceChildren();

  const cells = boardData.squares.map((sq) => {
    const { row, col, side } = cellPos(sq.id);
    const corner = sq.id % 10 === 0;
    const cell = document.createElement("div");
    cell.className = `mm-sq side-${side}${corner ? " corner" : ""}`;
    cell.style.gridArea = `${row} / ${col}`;
    cell.title = sq.name;

    const group = sq.group && boardData.groups[sq.group];
    if (group) {
      const band = document.createElement("div");
      band.className = "mm-band";
      band.style.background = group.color;
      cell.append(band);
    }
    const body = document.createElement("div");
    body.className = "mm-body";
    if (ICONS[sq.id]) body.append(span("mm-icon", ICONS[sq.id]));
    body.append(span("mm-name", sq.name));
    if (sq.price) body.append(span("mm-price", `🪙${sq.price}`));
    else if (sq.amount) body.append(span("mm-price", `pay ${sq.amount}`));
    cell.append(body);
    el.append(cell);
    return cell;
  });

  const center = document.createElement("div");
  center.className = "mm-center";
  el.append(center);

  // Tokens float on a layer above the grid so they can glide between squares.
  const layer = document.createElement("div");
  layer.className = "mm-tokens";
  el.append(layer);

  const tokens = {};  // player id -> {el, shown, target, lane, timer}

  function place(t, animate) {
    const cell = cells[t.shown];
    const w = cell.offsetWidth, h = cell.offsetHeight;
    const size = Math.min(w, h) * 0.42;
    const x = cell.offsetLeft + w * (0.23 + 0.27 * (t.lane - 1)) - size / 2;
    const y = cell.offsetTop + h * 0.74 - size / 2;  // low in the square, under the name
    const ms = Math.max(t.stepMs - 20, 0);
    t.el.style.transition = animate ? `left ${ms}ms ease-in-out, top ${ms}ms ease-in-out` : "none";
    Object.assign(t.el.style, { left: `${x}px`, top: `${y}px`, width: `${size}px`, height: `${size}px`, fontSize: `${size * 0.5}px` });
  }

  // Walk forward one square at a time (wrapping past 39), like the real belts.
  function walk(t) {
    if (t.timer) return;  // already walking; it will keep going to the new target
    const steps = (t.target - t.shown + 40) % 40;
    t.stepMs = Math.min(STEP_MS, MAX_WALK_MS / steps);
    const stepOnce = () => {
      if (t.shown === t.target) { t.timer = null; return; }
      t.shown = (t.shown + 1) % 40;
      place(t, true);
      t.timer = setTimeout(stepOnce, t.stepMs);
    };
    stepOnce();
  }

  function render(state) {
    const owners = state.owners || {};
    cells.forEach((cell, i) => {
      const owner = owners[String(i)];
      cell.style.setProperty("--owner", owner ? `var(--${owner})` : "transparent");
      cell.classList.toggle("owned", !!owner);
    });

    for (const p of state.players) {
      let t = tokens[p.id];
      if (!t) {
        const tokEl = document.createElement("div");
        tokEl.className = "mm-token";
        tokEl.style.background = `var(--${p.id})`;
        tokEl.textContent = tokenGlyph(p);
        tokEl.title = p.name;
        layer.append(tokEl);
        t = tokens[p.id] = { el: tokEl, shown: p.position, target: p.position, lane: p.lane, timer: null, stepMs: STEP_MS };
        place(t, false);
      }
      t.el.hidden = p.bankrupt;
      // While the motor is moving this token, walk toward where it's going so
      // the screen moves together with the real board (not after move_done).
      const move = state.pending?.kind === "move" && state.pending.player === p.id ? state.pending : null;
      t.target = move ? move.to : p.position;
      if (t.shown !== t.target) walk(t);
    }
  }

  // Re-measure when the board changes size (phone rotation, window resize).
  new ResizeObserver(() => {
    el.style.fontSize = `${el.clientWidth / 62}px`;
    Object.values(tokens).forEach((t) => place(t, false));
  }).observe(el);

  return { render, center };
}

function span(cls, text) {
  const s = document.createElement("span");
  s.className = cls;
  s.textContent = text;
  return s;
}

let stylesDone = false;
function injectStyles() {
  if (stylesDone) return;
  stylesDone = true;
  const css = `
  .mm-board {
    position: relative; width: 100%; aspect-ratio: 1;
    display: grid; gap: 2px; padding: 2px;
    grid-template-columns: 1.6fr repeat(9, 1fr) 1.6fr;
    grid-template-rows: 1.6fr repeat(9, 1fr) 1.6fr;
    background: #6b4f2a; border-radius: 12px;
    box-shadow: 0 10px 40px rgba(0,0,0,.5), 0 0 0 4px rgba(244,201,93,.35);
  }
  .mm-sq {
    background: var(--parchment); color: var(--parchment-ink);
    display: flex; overflow: hidden; min-width: 0; min-height: 0; border-radius: 3px;
    box-shadow: inset 0 0 0 0 var(--owner);
  }
  .mm-sq.owned { box-shadow: inset 0 0 0 .35em var(--owner); }
  .mm-sq.side-b { flex-direction: column; }
  .mm-sq.side-t { flex-direction: column-reverse; }
  .mm-sq.side-l { flex-direction: row-reverse; }
  .mm-sq.side-r { flex-direction: row; }
  .mm-band { flex: 0 0 22%; }
  .mm-body {
    flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: center;
    justify-content: flex-start; text-align: center; padding: .25em .15em; gap: .1em; line-height: 1.05;
  }
  .mm-icon { font-size: 1.6em; line-height: 1; }
  .corner .mm-icon { font-size: 2.6em; }
  .mm-name { font-size: .78em; font-weight: 600; overflow-wrap: anywhere; }
  .corner .mm-name { font-size: 1em; }
  .mm-price { font-size: .7em; opacity: .75; }
  .mm-center {
    grid-area: 2 / 2 / 11 / 11; display: flex; flex-direction: column;
    align-items: center; justify-content: center; text-align: center; gap: .6em; padding: 1em;
    background: radial-gradient(circle, #3a2a70 0%, #20163f 75%); border-radius: 6px; color: var(--ink);
    font-size: 1.6em; overflow: hidden;
  }
  .mm-tokens { position: absolute; inset: 0; pointer-events: none; }
  .mm-token {
    position: absolute; border-radius: 50%; display: grid; place-items: center;
    color: #fff; font-weight: 700; border: 2px solid #fff;
    box-shadow: 0 3px 8px rgba(0,0,0,.45);
  }`;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.append(style);
}

// One sentence describing what the game is waiting for, from `viewer`'s
// point of view ("board" for the laptop, or a player id for a kid's phone).
export function statusText(state, boardData, viewer) {
  const name = (pid) => state.players.find((p) => p.id === pid)?.name ?? "the bank";
  const cur = state.players.find((p) => p.id === state.current_player);
  const pend = state.pending;
  const place = (i) => boardData.squares[i].name;
  switch (state.phase) {
    case "LOBBY": return "";
    case "WAITING_FOR_ROLL":
      if (cur.kind === "in_person") return `${cur.name}, roll the dice into the tray`;
      return viewer === cur.id ? "Tap Roll!" : `Waiting for ${cur.name} to roll`;
    case "MOVING": return `${name(pend.player)} is on the move to ${place(pend.to)}…`;
    case "WAITING_FOR_DECISION": {
      const who = state.players.find((p) => p.id === pend.player);
      if (pend.type === "jail") {
        if (viewer === who.id) return `Pay 🪙${pend.price} to leave Rapunzel's Tower?`;
        if (who.kind === "in_person") return `${who.name}, pay 🪙${pend.price} to leave the tower? Say yes or no`;
        return `${who.name} is deciding whether to leave the tower`;
      }
      if (viewer === who.id) return `Buy ${place(pend.square)} for 🪙${pend.price}?`;
      if (who.kind === "in_person") return `${who.name}, buy ${place(pend.square)} for 🪙${pend.price}? Say yes or no`;
      return `${who.name} is deciding about ${place(pend.square)}`;
    }
    case "WAITING_FOR_PAYMENT": {
      const what = {
        buy: `for ${place(pend.square)}`,
        tax: `for the ${place(pend.square)}`,
        jail_fine: "to leave the tower",
      }[pend.reason] || `to ${name(pend.payee)}`;
      return `${name(pend.payer)}, tap your card to pay 🪙${pend.amount} ${what}`;
    }
    case "WAITING_FOR_CARD": {
      const deck = pend.deck === "spell" ? "a Spell" : "a Fairy Godmother card";
      const grandma = state.players[0];
      if (viewer === pend.for) return `${grandma.name} is drawing ${deck} for you…`;
      return `${grandma.name}, please draw ${deck} for ${pend.for === grandma.id ? "yourself" : name(pend.for)}`;
    }
    case "GAME_OVER": return `${name(state.winner)} wins the kingdom! 👑`;
    default: return "";
  }
}
