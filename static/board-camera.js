// Presentation only: optional 3D views or the flat board. No game actions.
import { statusText } from "/static/board.js";

export function mountBoardCamera(board, boardData, gameId) {
  const viewport = board.parentElement;
  const follow = document.getElementById("view-follow");
  const full = document.getElementById("view-full");
  const label = document.getElementById("board-view-location");
  const caption = document.getElementById("board-view-status");
  const key = `magic-monopoly:board-view:${gameId}`;
  const renderKey = "magic-monopoly:board-3d";
  let threeD = true;
  try { threeD = localStorage.getItem(renderKey) !== "off"; } catch { }
  let mode = "follow", state = null, movement = null, releaseTimer = null, frame = null;
  try { if (localStorage.getItem(key) === "full") mode = "full"; } catch { }

  let world = null, loading = false, unavailable = false, queuedRoll = null;
  function failWorld() {
    unavailable = true; loading = false; mode = "full";
    queuedRoll = null;
    world?.clearDice();
    world?.setVisible(false);
    const canvas = viewport.querySelector('.board-world'); if (canvas) canvas.hidden = true;
    follow.disabled = true; follow.title = '3D is unavailable in this browser. The full board remains available.';
    draw(false);
  }
  function ensureWorld() {
    if (world || loading || unavailable) return;
    loading = true;
    Promise.all([
      import('/static/board-world.js'),
      // Canvas labels are baked once; wait for their actual font before drawing.
      document.fonts.load('49px "Planewalker"'),
      document.fonts.load('700 49px "Planewalker"'),
    ]).then(([{ createBoardWorld }]) => {
      // Toggling off during loading must not create or reveal a WebGL scene.
      if (!threeD || unavailable) { loading = false; queuedRoll = null; draw(false); return; }
      world = createBoardWorld(viewport, boardData, failWorld);
      loading = false;
      if (state) world.updateState(state);
      draw(false);
      if (threeD && queuedRoll && performance.now() - queuedRoll.at < 2000) world.rollDice(queuedRoll.event);
      queuedRoll = null;
    }).catch(error => { console.warn('3D board unavailable:', error); failWorld(); });
  }

  const tokenFor = id => [...board.querySelectorAll(".mm-token")].find(t => t.dataset.player === id);
  function activePlayer() {
    if (!state) return null;
    const id = movement?.player || state.winner || state.current_player;
    return state.players.find(p => p.id === id && !p.bankrupt) || state.players.find(p => !p.bankrupt);
  }
  function schedule() {
    if (frame !== null) return;
    frame = requestAnimationFrame(() => { frame = null; draw(); });
  }
  function draw(animate = true) {
    const player = activePlayer();
    const token = player && tokenFor(player.id);
    const wants3D = !!state && threeD && !unavailable;
    if (wants3D) ensureWorld();
    const rendered = wants3D && !!world;
    const zoomed = rendered && mode === "follow" && state.phase !== "LOBBY" && !!token;
    viewport.dataset.view = zoomed ? "follow" : "full";
    viewport.dataset.render = rendered ? "3d" : "flat";
    viewport.dataset.threeD = String(threeD && !unavailable);
    viewport.dataset.threeDAvailable = String(!unavailable);
    follow.disabled = !threeD || unavailable;
    follow.title = unavailable ? '3D is unavailable in this browser.' : !threeD ? 'Turn 3D on to follow the token.' : 'Follow the moving token';
    const selectedMode = !threeD || unavailable ? 'full' : mode;
    follow.setAttribute("aria-pressed", String(selectedMode === "follow"));
    full.setAttribute("aria-pressed", String(selectedMode === "full"));
    caption.textContent = state?.phase === "LOBBY"
      ? "Players are gathering on the home screen. Start the game when everyone is ready."
      : state ? statusText(state, boardData, "board") : "Connecting to the story…";
    const square = token ? Number(token.dataset.square) : player?.position;
    label.textContent = zoomed
      ? `Following ${player.name} · ${boardData.squares[square]?.name || "on the move"}`
      : wants3D && loading ? "Opening the storybook world…" : !threeD || unavailable ? "Flat board · All 40 spaces" : "All 40 spaces";
    board.style.transition = "none";
    board.style.transform = "none";
    world?.focus(player?.id);
    world?.setMode(zoomed ? "follow" : "full");
    world?.setVisible(rendered);
    if (unavailable) caption.textContent += " The 3D view is unavailable here; use the full board.";
    // The server can finish a turn before the visual token finishes walking.
    // Hold the camera on that token through its last step, then follow the next turn.
    if (movement && state?.pending?.kind !== "move") {
      const movingToken = tokenFor(movement.player);
      if (movingToken?.dataset.square === String(movement.to) && movingToken.dataset.moving !== "true" && releaseTimer === null) {
        releaseTimer = setTimeout(() => { movement = null; releaseTimer = null; schedule(); }, 220);
      }
    }
  }
  function setMode(value) {
    mode = value;
    world?.resetView();
    try { localStorage.setItem(key, mode); } catch { }
    draw(false);
  }
  viewport.addEventListener('board-render-toggle', () => {
    if (unavailable) return;
    threeD = !threeD;
    queuedRoll = null;
    world?.clearDice();
    try { localStorage.setItem(renderKey, threeD ? 'on' : 'off'); } catch { }
    draw(false);
  });
  follow.addEventListener("click", () => { if (!follow.disabled) setMode("follow"); });
  full.addEventListener("click", () => setMode("full"));
  new MutationObserver(records => {
    if (records.some(r => r.type === "childList" || r.target.classList?.contains("mm-token"))) schedule();
  }).observe(board, { childList: true, subtree: true, attributes: true, attributeFilter: ["style", "data-square", "data-moving"] });
  new ResizeObserver(() => draw(false)).observe(viewport);
  draw(false);
  return {
    onEvent(event) {
      if (event.type !== 'rolled') return;
      if (!Array.isArray(event.dice)) { queuedRoll = null; world?.clearDice(); return; }
      if (!threeD || unavailable) return;
      const roll = { ...event, from: state?.pending?.kind === 'move' ? state.pending.from : state?.players.find(p => p.id === event.player)?.position };
      if (world) world.rollDice(roll);
      else queuedRoll = { event: roll, at: performance.now() };
    },
    updateState(next) {
      state = next;
      world?.updateState(next);
      if (state.phase === "LOBBY") {
        movement = null;
        clearTimeout(releaseTimer); releaseTimer = null;
      } else if (state.pending?.kind === "move") {
        const pending = state.pending;
        if (movement?.player !== pending.player || movement?.to !== pending.to) {
          clearTimeout(releaseTimer); releaseTimer = null;
          movement = { player: pending.player, to: pending.to };
        }
      }
      if (movement && !state.players.some(p => p.id === movement.player && !p.bankrupt)) movement = null;
      draw();
    },
  };
}
