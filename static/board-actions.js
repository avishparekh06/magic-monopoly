// Board-only presentation and controls. All choices still go through the engine.
import { DICE_TOSS_MS } from '/static/board-motion.js';
import { h } from '/static/net.js';
import { statusText } from '/static/board.js';
import { mountBoardPlayers } from '/static/board-players.js';
import { mountSessionHub } from '/static/session-hub.js';

export function mountBoardActions(viewport, board, cards, send) {
  let state = null, connected = false, busy = false, receipt = null, error = '';
  let pendingKey = '', diceRolling = false, waitingForDice = false, lastDigitalRoll = null;
  const status = h('div', { class: 'game-turn-status', role: 'status' });
  const content = h('section', { id: 'turn-card', class: 'game-action-card', 'aria-label': 'Current board action' });
  const errorEl = h('p', { class: 'game-action-error', role: 'alert', hidden: true });
  const fullscreen = h('button', { id: 'game-fullscreen', class: 'btn small secondary', type: 'button', 'aria-pressed': 'false' }, 'Fullscreen');
  const view = h('button', { id: 'game-map-toggle', class: 'btn small secondary', type: 'button' }, 'Full board');
  const threeD = h('button', { id: 'game-3d-toggle', class: 'btn small secondary', type: 'button', 'aria-label': '3D board', 'aria-pressed': 'false', onclick: () => viewport.dispatchEvent(new CustomEvent('board-render-toggle')) }, '3D off');
  const library = mountSessionHub({ parent: viewport });
  const players = mountBoardPlayers(viewport, board, () => library.openGame(state.game_id));
  const browse = h('button', { id: 'game-players', class: 'btn small secondary', type: 'button', onclick: () => players.open(), title: 'Players, owned properties, game history and stats' }, 'Players & stats');
  const reset = h('button', { id: 'game-reset-view', class: 'btn small secondary game-reset-view', type: 'button', title: 'Reset camera (R)' }, 'Reset view');
  view.title = 'Switch between the board map and following the token (M)';
  const tools = h('div', { class: 'game-surface-tools' }, threeD, view, reset, browse, fullscreen);
  const rollLabel = h('span', { class: 'game-roll-label' }, 'Roll Dice');
  const rollWho = h('span', { class: 'game-roll-who' });
  const rollButton = h('button', { id: 'turn-roll', class: 'game-roll-dice', type: 'button', hidden: true, onclick: () => {
    if (!state || busy || !connected || state.phase !== 'WAITING_FOR_ROLL') return;
    const action = { action: 'board_roll', target: state.current_player, turn: state.turn };
    busy = true; error = ''; render(); send(action);
  } }, rollLabel, rollWho);
  const rollResult = h('div', { id: 'dice-roll-result', class: 'game-roll-result', role: 'status', hidden: true });
  const fallback = h('div', { class: 'game-dice-fallback', 'aria-hidden': 'true' });
  let settleTimer = null, resultTimer = null, fallbackSquare = null;

  const overlay = h('div', { class: 'game-overlay' }, status, tools, h('div', { class: 'game-action-stack' }, errorEl, content), players.strip, rollButton, rollResult, fallback);
  viewport.append(overlay);
  // Overlay interactions must not orbit the camera or trigger keyboard shortcuts.
  for (const event of ['pointerdown', 'keydown', 'wheel']) overlay.addEventListener(event, e => {
    if (e.target.closest('button, select, details, .game-action-card')) e.stopPropagation();
  });
  const player = id => state?.players.find(p => p.id === id);
  const name = id => player(id)?.name || id;
  const button = (label, id, action, secondary = false) => h('button', {
    type: 'button', id, class: `btn small${secondary ? ' secondary' : ''}`, disabled: busy || !connected,
    onclick: () => { if (busy || !connected) return; busy = true; error = ''; render(); send(action); }
  }, label);
  const paragraph = text => h('p', {}, text);
  const heading = (eyebrow, title, color = '#b88c40') => [
    h('div', { class: 'game-card-band', style: `background:${color}` }),
    h('p', { class: 'game-card-eyebrow' }, eyebrow), h('h2', {}, title)
  ];
  function deed(square, who) {
    const owner = state.owners?.[String(square.id)];
    return [
      ...heading(`${name(who)} landed here`, square.name, board.groups[square.group]?.color),
      square.price != null ? h('div', { class: 'game-deed-price' }, `${square.price} gold`) : null,
      square.rent != null ? paragraph(`Base rent: ${square.rent} gold`) : null,
      square.type === 'railroad' ? paragraph(`Rent: ${board.railroad_rent.join(' / ')} gold for 1 / 2 / 3 / 4 workshops.`) : null,
      square.type === 'utility' ? paragraph(`Rent: ${board.utility_multiplier.join(' or ')} × the dice total, depending on how many utilities the owner has.`) : null,
      square.type === 'tax' ? paragraph(`Pay ${square.amount} gold to the bank.`) : null,
      owner ? paragraph(`Owned by ${name(owner)}`) : null
    ];
  }
  function drawReceipt() {
    if (!receipt) return [];
    const close = h('button', { class: 'game-card-close', type: 'button', 'aria-label': 'Dismiss card', onclick: () => { receipt = null; render(); } }, 'Close');
    if (receipt.card) {
      const c = receipt.card, deck = cards.decks[c.deck];
      return [h('div', { class: 'game-drawn-card', id: 'drawn-card' }, close,
        ...heading(`${name(receipt.player)} · ${deck.name}`, c.title, deck.color), paragraph(c.text))];
    }
    return [h('div', { class: 'game-landed-card' }, close, ...deed(board.squares[receipt.square], receipt.player))];
  }
  viewport.addEventListener("board-motion-change", () => render());
  function render() {
    if (!state) return;
    const moving = !!viewport.querySelector('.mm-token[data-moving="true"]');
    const p = state.pending;
    rollButton.hidden = state.phase !== 'WAITING_FOR_ROLL' || diceRolling || moving || busy;
    rollButton.disabled = busy || !connected;
    rollWho.textContent = `${name(state.current_player)}'s turn`;
    rollLabel.textContent = busy ? 'Rolling…' : 'Roll Dice';
    overlay.dataset.rollReady = String(!rollButton.hidden);
    browse.disabled = state.phase === 'LOBBY';
    syncView();
    status.textContent = connected ? (diceRolling ? (waitingForDice ? 'Getting the dice ready…' : 'Rolling the dice…') : moving ? 'Moving to the next space…' : statusText(state, board, 'board')) : 'Connecting to the game…';
    errorEl.textContent = error; errorEl.hidden = !error;
    let body = [];
    if (state.phase === 'WAITING_FOR_DECISION' && p?.kind === 'decision') {
      if (p.type === 'buy') {
        body = [...deed(board.squares[p.square], p.player),
          paragraph(`${name(p.player)} has ${player(p.player).balance} gold. Would you like to buy this place?`),
          h('div', { class: 'game-action-buttons' },
            button(`Buy · ${p.price} gold`, 'turn-buy', { action: 'decision', player: p.player, answer: 'yes' }),
            button('No thanks', 'turn-decline', { action: 'decision', player: p.player, answer: 'no' }, true))];
      } else {
        body = [...heading(`${name(p.player)}'s choice`, "Escape Rapunzel's Tower"), paragraph(`Pay ${p.price} gold to leave, or stay for this turn.`),
          h('div', { class: 'game-action-buttons' },
            button(`Pay ${p.price} gold`, 'turn-buy', { action: 'decision', player: p.player, answer: 'yes' }),
            button('Stay this turn', 'turn-decline', { action: 'decision', player: p.player, answer: 'no' }, true))];
      }
    } else if (state.phase === 'WAITING_FOR_PAYMENT' && p?.kind === 'payment') {
      const reasons = { buy: 'Purchase', rent: 'Rent', tax: 'Tax', jail_fine: 'Tower release', card: 'Card payment' };
      body = [...heading(`${name(p.payer)} · ${reasons[p.reason] || 'Payment'}`, `${p.amount} gold`),
        p.square != null ? paragraph(board.squares[p.square].name) : null,
        paragraph(`To ${p.payee === 'bank' ? 'the bank' : name(p.payee)}. Present ${name(p.payer)}'s player card to the reader.`),
        h('details', { class: 'game-manual-input' }, h('summary', {}, 'Card reader not available?'),
          paragraph('Use the existing manual payment fallback.'), button('Confirm payment', 'turn-confirm', { action: 'confirm_payment' }))];
    } else if (state.phase === 'WAITING_FOR_CARD' && p?.kind === 'card') {
      const deck = cards.decks[p.deck];
      const picker = h('select', { id: 'turn-card-picker', 'aria-label': 'Physical card drawn' },
        cards.cards.filter(c => c.deck === p.deck).map(c => h('option', { value: c.id }, `${c.title} (${c.id})`)));
      const readButton = h('button', { id: 'turn-read-card', type: 'button', class: 'btn small', disabled: busy || !connected, onclick: () => {
        if (busy || !connected) return;
        const id = picker.value; busy = true; error = ''; render(); send({ source: 'vision', type: 'card', id });
      } }, 'Show this card');
      body = [...heading(`${name(p.for)} draws a card`, deck.name, deck.color),
        paragraph('Draw a physical card and hold it to the camera. Its text will appear here when read.'),
        h('details', { class: 'game-manual-input' }, h('summary', {}, 'Choose a card manually (simulation)'), picker, readButton)];
    } else if (state.phase === 'LOBBY') {
      const waiting = state.players.filter(p => !p.connected);
      body = [...heading('A new story awaits', 'Ready to play?'), paragraph(waiting.length ? `Waiting for ${waiting.map(p => p.name).join(', ')}.` : 'Everyone is here.'),
        waiting.length ? null : button('Start the game', 'turn-start', { action: 'start' })];
    } else if (state.phase === 'GAME_OVER') {
      body = [...heading('The final chapter', `${name(state.winner)} wins`),
        h('button', { class: 'btn small', type: 'button', onclick: () => library.openGame(state.game_id) }, 'Game results & stats'),
        h('a', { class: 'btn small', href: '/' }, 'Back to homepage')];
    }
    const receiptNodes = drawReceipt();
    // A required choice stays visible alongside any card that caused it.
    content.replaceChildren(...receiptNodes, ...(body.length ? [h('div', { class: 'game-required-action' }, ...body)] : []));
    content.hidden = diceRolling || moving || (!body.length && !receiptNodes.length);
    const oldDice = overlay.querySelector('.game-dice-entry');
    oldDice?.remove();
    if (state.phase === 'WAITING_FOR_ROLL') {
      const dice = h('select', { 'aria-label': 'Dice total', id: 'turn-dice-total' },
        Array.from({ length: 11 }, (_, i) => h('option', { value: i + 2 }, `${i + 2}`)));
      const submit = h('button', { id: 'turn-dice-read', type: 'button', class: 'btn small secondary', disabled: busy || !connected, onclick: () => {
        const value = Number(dice.value); if (busy || !connected) return;
        busy = true; render(); send({ source: 'vision', type: 'dice', value });
      } }, 'Read total');
      overlay.append(h('details', { class: 'game-dice-entry' }, h('summary', {}, 'Enter physical dice (simulation)'),
        h('div', { class: 'game-action-buttons' }, dice, submit)));
    }
  }
  function syncFullscreen() {
    const active = document.fullscreenElement === viewport || document.webkitFullscreenElement === viewport;
    fullscreen.textContent = active ? 'Exit fullscreen' : 'Fullscreen';
    fullscreen.setAttribute('aria-pressed', String(active));
  }
  fullscreen.onclick = async () => {
    try {
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        await (document.exitFullscreen?.() ?? document.webkitExitFullscreen?.());
      } else {
        const request = viewport.requestFullscreen || viewport.webkitRequestFullscreen;
        if (!request) throw Error('Fullscreen is unavailable in this browser.');
        await request.call(viewport);
      }
    } catch (e) { error = e.message; render(); }
  };
  // Some embedded browsers forward Escape to the page rather than the native UI.
  document.addEventListener('keydown', async e => {
    if (e.key === 'Escape' && viewport.querySelector('dialog[open]')) {
      e.preventDefault(); viewport.querySelector('dialog[open]').close(); return;
    }
    if (e.key !== 'Escape' || (document.fullscreenElement !== viewport && document.webkitFullscreenElement !== viewport)) return;
    try { await (document.exitFullscreen?.() ?? document.webkitExitFullscreen?.()); }
    catch (e) { error = e.message; render(); }
  }, true);
  document.addEventListener('fullscreenchange', syncFullscreen);
  document.addEventListener('webkitfullscreenchange', syncFullscreen);
  view.onclick = () => document.getElementById(viewport.dataset.view === 'full' ? 'view-follow' : 'view-full').click();
  reset.onclick = () => document.getElementById(viewport.dataset.view === 'full' ? 'view-full' : 'view-follow').click();
  // A queued 3D roll starts after the current square is visible, not at packet arrival.
  viewport.addEventListener('board-dice-wait', () => {
    if (!lastDigitalRoll) return;
    clearTimeout(settleTimer); clearTimeout(resultTimer);
    waitingForDice = true; diceRolling = true; rollResult.hidden = true; render();
  }, true);
  viewport.addEventListener('board-dice-start', () => {
    if (!lastDigitalRoll) return;
    const m = lastDigitalRoll;
    clearTimeout(settleTimer); clearTimeout(resultTimer);
    waitingForDice = false; diceRolling = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    rollResult.hidden = false; rollResult.textContent = `${name(m.player)} is rolling…`; render();
    settleTimer = setTimeout(() => { rollResult.textContent = `${name(m.player)} rolled ${m.value}`;fallback.replaceChildren();diceRolling = false;render(); }, diceRolling ? DICE_TOSS_MS : 0);
    resultTimer = setTimeout(() => { rollResult.hidden = true; }, 5200);
  }, true);
  viewport.addEventListener('board-dice-cancel', () => {
    if (!lastDigitalRoll || (!diceRolling && !waitingForDice)) return;
    clearTimeout(settleTimer); clearTimeout(resultTimer);
    waitingForDice = false; diceRolling = false; fallback.replaceChildren();
    rollResult.hidden = false; rollResult.textContent = `${name(lastDigitalRoll.player)} rolled ${lastDigitalRoll.value}`;
    resultTimer = setTimeout(() => { rollResult.hidden = true; }, 5200); render();
  }, true);
  function placeFlatDice() {
    if (viewport.dataset.render === '3d' || fallbackSquare == null || !fallback.children.length) return;
    const cell = viewport.querySelectorAll('#board .mm-sq')[fallbackSquare];
    if (!cell) return;
    const rect = cell.getBoundingClientRect(), surface = viewport.getBoundingClientRect();
    const size = Math.min(rect.width, rect.height) * .24;
    fallback.classList.add('is-local');
    fallback.dataset.square = String(fallbackSquare);
    Object.assign(fallback.style, { inset: 'auto', left: `${rect.left-surface.left}px`, top: `${rect.top-surface.top}px`, width: `${rect.width}px`, height: `${rect.height}px`, paddingTop: `${rect.height*.16}px`, gap: `${size*.18}px` });
    for (const die of fallback.children) Object.assign(die.style, { width: `${size}px`, height: `${size}px`, gap: `${size*.07}px`, padding: `${size*.12}px`, borderWidth: '1px', borderRadius: `${size*.16}px`, boxShadow: '1px 1px 1px #0004' });
  }
  new ResizeObserver(placeFlatDice).observe(viewport);
  function syncView() {
    view.textContent = viewport.dataset.view === 'full' ? 'Follow token' : 'Full board';
    view.disabled = state?.phase === 'LOBBY' || (viewport.dataset.view === 'full' && document.getElementById('view-follow').disabled);
    const enabled = viewport.dataset.threeD !== 'false';
    const available = viewport.dataset.threeDAvailable !== 'false';
    threeD.textContent = available ? (enabled ? '3D on' : '3D off') : '3D unavailable';
    threeD.setAttribute('aria-pressed', String(enabled && available));
    threeD.disabled = !available;
    threeD.title = available ? (enabled ? 'Switch to the flat board' : 'Switch to the 3D board') : 'This browser can use the flat board';
    view.hidden = !enabled || !available;
    reset.hidden = !enabled || !available;
    reset.disabled = viewport.dataset.render !== '3d';
    placeFlatDice();
  }
  new MutationObserver(syncView).observe(viewport, { attributes: true, attributeFilter: ['data-view', 'data-render', 'data-three-d', 'data-three-d-available'] });
  syncView();
  document.addEventListener('keydown', e => {
    if (e.defaultPrevented || e.repeat || e.ctrlKey || e.metaKey || e.altKey || state?.phase === 'LOBBY'
        || viewport.querySelector('dialog[open]') || e.target.closest('input, select, textarea, button, [contenteditable]')) return;
    if (e.key.toLowerCase() === 'm' && !view.disabled) { e.preventDefault(); view.click(); }
    if (e.key.toLowerCase() === 'r' && !reset.disabled) { e.preventDefault(); reset.click(); }
  });
  return {
    openStats() { if (state) library.openGame(state.game_id); },
    update(s) {
      const key = JSON.stringify([s.phase, s.turn, s.pending]);
      if (key !== pendingKey) { busy = false; error = ''; pendingKey = key; }
      if (s.phase === 'MOVING' && state?.phase !== 'MOVING') receipt = null;
      if (state && s.turn !== state.turn && receipt && !receipt.card) receipt = null;   // a new turn: the last landing is old news
      // Restore an unresolved card payment after a reconnect, without replaying old cards.
      if (!state && s.pending?.reason === 'card' && s.last_card) {
        const card = cards.cards.find(c => c.id === s.last_card.id);
        if (card) receipt = { card, player: s.last_card.for };
      }
      state = s; players.update(s); render();
    },
    event(m) {
      if (!state) return;
      if (m.type === 'rolled') {
        clearTimeout(settleTimer); clearTimeout(resultTimer); diceRolling = false; waitingForDice = false; lastDigitalRoll = Array.isArray(m.dice) ? m : null;
        fallback.replaceChildren(); fallbackSquare = null; rollResult.hidden = false;
        const faces = m.dice;
        if (Array.isArray(faces) && faces.length === 2) {
          diceRolling = !matchMedia('(prefers-reduced-motion: reduce)').matches;
          rollResult.textContent = `${name(m.player)} is rolling…`;
          render();
          fallbackSquare = state.pending?.kind === 'move' && state.pending.player === m.player ? state.pending.from : player(m.player)?.position;
          const dots = { 1: [5], 2: [1,9], 3: [1,5,9], 4: [1,3,7,9], 5: [1,3,5,7,9], 6: [1,3,4,6,7,9] };
          for (const value of faces) fallback.append(h('div', { class: 'game-fallback-die', 'data-value': value },
            ...Array.from({ length: 9 }, (_, i) => h('span', { class: dots[value].includes(i + 1) ? 'pip' : '' }))));
          placeFlatDice();
          settleTimer = setTimeout(() => {
            rollResult.textContent = `${name(m.player)} rolled ${m.value}`;
            fallback.replaceChildren(); diceRolling = false; render();
          }, matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : DICE_TOSS_MS);
        } else { rollResult.textContent = `${name(m.player)} rolled ${m.value}`; render(); }
        resultTimer = setTimeout(() => { rollResult.hidden = true; }, 5200);
        return;
      }
      if (m.type === 'card_drawn') {
        const card = cards.cards.find(c => c.id === m.id);
        if (card) receipt = { card, player: m.player };
      } else if (m.type === 'landed' && !receipt?.card && !['WAITING_FOR_DECISION', 'WAITING_FOR_CARD'].includes(state.phase)) {
        receipt = { square: m.square, player: m.player };
      } else return;
      render();
    },
    error(message) { busy = false; error = message; render(); },
    connection(value) { connected = value === 'online'; if (!connected) busy = false; render(); }
  };
}
