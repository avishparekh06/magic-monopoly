// Shared 2D board renderer, used by the board laptop page and the kid app.
//
//   const board = createBoardView(containerEl, await loadBoard());
//   board.render(state);        // call on every state message
//   board.center                // the empty middle; pages put their own content here
//
// Layout: an 11x11 CSS grid where only the outer ring holds squares.
// Square 0 (Once Upon a Time) is bottom-right; play runs left along the bottom,
// up the left side, right along the top and down the right side.

import { tokenGlyph } from "/static/net.js";
import { DICE_TOSS_MS, DICE_HOLD_MS, TOKEN_STEP_MS, TOKEN_LANDING_MS, MAX_TOKEN_WALK_MS } from '/static/board-motion.js';
import { boardIcon } from "/static/icons.js";


// Movement timing is shared with the presentation regression checks.

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
    const illustration = boardIcon(sq.id);
    if (illustration) body.append(illustration);
    body.append(span("mm-name", sq.name));
    if (sq.price) body.append(span("mm-price", `${sq.price} G`));
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

  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const motionChanged = () => el.dispatchEvent(new CustomEvent("board-motion-change", { bubbles: true }));
  let previousPhase = null;
  const tokens = {};  // player id -> {el, shown, target, lane, timer}

  function holdForDice(player) {
    const t = tokens[player];
    if (t && !reducedMotion.matches)
      t.holdUntil = performance.now() + DICE_TOSS_MS + DICE_HOLD_MS;
  }
  // The 3D scene may finish loading after the roll message. Start its hold
  // from the actual toss, so the first hop never races the dice animation.
  el.addEventListener('board-dice-start', event => holdForDice(event.detail.player));
  el.addEventListener('board-dice-wait', event => {
    const t = tokens[event.detail.player];
    if (t && !reducedMotion.matches) t.holdUntil = performance.now() + 6000;
  });
  el.addEventListener('board-dice-cancel', event => {
    const t = tokens[event.detail.player];
    if (t) t.holdUntil = t.rollDeadline || 0;
  });

  function place(t, animate) {
    t.el.dataset.square = String(t.shown);
    // Keep animation coordinates meaningful while fullscreen/view changes resize the board.
    t.el.dataset.layoutWidth = String(el.clientWidth);
    t.el.dataset.layoutHeight = String(el.clientHeight);
    const cell = cells[t.shown];
    const w = cell.offsetWidth, h = cell.offsetHeight;
    const size = Math.min(w, h) * 0.42;
    const x = cell.offsetLeft + w * (0.23 + 0.27 * (t.lane - 1)) - size / 2;
    const y = cell.offsetTop + h * 0.74 - size / 2;  // low in the square, under the name
    const ms = Math.max(t.stepMs - TOKEN_LANDING_MS, 0);
    if (animate) {
      t.el.dataset.hopStart = String(performance.now());
      t.el.dataset.hopDuration = String(ms);
      t.hop?.cancel();
      t.hop = t.el.animate([
        { transform: "translateY(0) scale(1)" },
        { transform: `translateY(-${Math.max(6, size * .35)}px) scale(1.02)`, offset: .5 },
        { transform: "translateY(0) scale(1)" },
      ], { duration: ms, easing: "ease-in-out" });
    }
    t.el.style.transition = animate ? `left ${ms}ms ease-in-out, top ${ms}ms ease-in-out` : "none";
    Object.assign(t.el.style, { left: `${x}px`, top: `${y}px`, width: `${size}px`, height: `${size}px`, fontSize: `${size * 0.5}px` });
  }

  // Queue destinations so a quick card/turn update never cuts a hop short.
  function walk(t) {
    if (t.timer !== null) return;
    t.el.dataset.moving = "true";
    motionChanged();
    const stepOnce = () => {
      const wait = t.holdUntil - performance.now();
      if (wait > 0 && !reducedMotion.matches) {
        t.timer = setTimeout(stepOnce, wait);
        return;
      }
      if (!t.journey) {
        t.journey = t.queue.shift();
        if (!t.journey) {
          t.timer = null;
          t.el.dataset.moving = "false";
          motionChanged();
          return;
        }
        const distance = ((t.journey.to - t.shown) * t.journey.direction + 40) % 40;
        t.stepMs = Math.min(TOKEN_STEP_MS, MAX_TOKEN_WALK_MS / Math.max(1, distance));
      }
      if (reducedMotion.matches) {
        t.shown = t.journey.to;
        place(t, false);
      } else if (t.shown !== t.journey.to) {
        t.shown = (t.shown + t.journey.direction + 40) % 40;
        place(t, true);
        t.timer = setTimeout(stepOnce, t.stepMs);
        return;
      }
      t.journey = null;
      t.timer = setTimeout(stepOnce, 0);
    };
    t.timer = setTimeout(stepOnce, 0);
  }

  let stalled = false;              // the motor reported a failed move: show tokens where the game has them
  function setStalled(v) { stalled = v; }

  function render(state) {
    const before = previousPhase;
    previousPhase = state.phase;
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
        tokEl.dataset.player = p.id;
        tokEl.style.background = `var(--${p.id})`;
        tokEl.textContent = tokenGlyph(p);
        tokEl.title = p.name;
        layer.append(tokEl);
        t = tokens[p.id] = { el: tokEl, shown: p.position, target: p.position, lane: p.lane, timer: null, stepMs: TOKEN_STEP_MS, queue: [], journey: null, holdUntil: 0 };
        place(t, false);
      }
      t.el.hidden = p.bankrupt;
      // While the motor is moving this token, walk toward where it's going so
      // the screen moves together with the real board (not after move_done).
      const move = !stalled && state.pending?.kind === "move" && state.pending.player === p.id ? state.pending : null;
      const target = move ? move.to : p.position;
      if ((stalled && t.shown !== p.position) || (target !== t.target && !move && before !== 'MOVING')) {
        if (t.timer !== null) clearTimeout(t.timer);
        t.timer = null; t.queue = []; t.journey = null; t.holdUntil = 0; t.rollDeadline = 0;
        t.hop?.cancel(); t.el.dataset.hopDuration = '0'; t.el.dataset.moving = 'false';
        t.target = target; t.shown = target; place(t, false); motionChanged();
      } else if (target !== t.target) {
        // The server sends MOVING before the rolled event. Establish the hold
        // here rather than hoping that event arrives within a short timeout.
        if (move && before === 'WAITING_FOR_ROLL' && Array.isArray(state.last_dice)) holdForDice(p.id);
        t.target = target;
        t.queue.push({ to: target, direction: move?.no_salary && !move.to_jail ? -1 : 1 });
        walk(t);
      }
    }
  }

  // Re-measure when the board changes size (phone rotation, window resize).
  new ResizeObserver(() => {
    el.style.fontSize = `${el.clientWidth / 62}px`;
    Object.values(tokens).forEach((t) => place(t, false));
  }).observe(el);

  return {
    render, center, setStalled,
    hasMotion: () => Object.values(tokens).some(t => t.el.dataset.moving === "true"),
    onEvent(event) {
      if (event.type !== "rolled") return;
      const t = tokens[event.player];
      if (Array.isArray(event.dice)) { holdForDice(event.player); if (t) t.rollDeadline = t.holdUntil; }
      else if (t) { t.holdUntil = 0; t.rollDeadline = 0; }
    },
  };
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
      if (viewer === "board") return `${cur.name}'s turn. Choose Roll Dice${cur.kind === "in_person" ? " or roll into the physical tray." : " here, or roll on their phone."}`;
      if (cur.kind === "in_person") return `${cur.name}, roll the dice into the tray`;
      return viewer === cur.id ? "Tap Roll!" : `Waiting for ${cur.name} to roll`;
    case "MOVING": return `${name(pend.player)} is on the move to ${place(pend.to)}…`;
    case "WAITING_FOR_DECISION": {
      const who = state.players.find((p) => p.id === pend.player);
      if (pend.type === "jail") {
        if (viewer === who.id) return `Pay ${pend.price} gold to leave Rapunzel's Tower?`;
        if (who.kind === "in_person") return `${who.name}, pay ${pend.price} gold to leave the tower? Say yes or no`;
        return `${who.name} is deciding whether to leave the tower`;
      }
      if (viewer === who.id) return `Buy ${place(pend.square)} for ${pend.price} gold?`;
      if (who.kind === "in_person") return `${who.name}, buy ${place(pend.square)} for ${pend.price} gold? Say yes or no`;
      return `${who.name} is deciding about ${place(pend.square)}`;
    }
    case "WAITING_FOR_PAYMENT": {
      // Card payments and tower fines have no square. Do not evaluate a
      // property lookup for those phases while constructing the prompt.
      const location = pend.square == null ? null : place(pend.square);
      const what = {
        buy: location ? `for ${location}` : 'for your purchase',
        tax: location ? `for the ${location}` : 'in tax',
        jail_fine: "to leave the tower",
      }[pend.reason] || `to ${name(pend.payee)}`;
      return `${name(pend.payer)}, lay your card on the reader to pay ${pend.amount} gold ${what}`;
    }
    case "WAITING_FOR_CARD": {
      const deck = pend.deck === "crystal" ? "a Crystal Ball card" : "a Chest of Spells card";
      const grandma = state.players[0];
      if (viewer === pend.for) return `${grandma.name} is drawing ${deck} for you…`;
      return `${grandma.name}, please draw ${deck} for ${pend.for === grandma.id ? "yourself" : name(pend.for)}`;
    }
    case "GAME_OVER": return `${name(state.winner)} wins the kingdom!`;
    default: return "";
  }
}
