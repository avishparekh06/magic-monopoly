// Read-only player overview and property browsing, including native fullscreen.
import { h, tokenDot } from '/static/net.js';
import { renderPropertyCards } from '/static/property-cards.js';

export function mountBoardPlayers(viewport, board, onStats) {
  let state = null, selected = null, context = '';
  const strip = h('nav', { class: 'game-player-strip', 'aria-label': 'Players, gold and owned places', hidden: true });
  const title = h('h2', { id: 'game-properties-title' });
  const tabs = h('div', { class: 'game-property-tabs', role: 'group', 'aria-label': 'Choose a player' });
  const details = h('div', { class: 'game-property-content' });
  const close = h('button', { class: 'btn small secondary', type: 'button', onclick: () => dialog.close() }, 'Back to board');
  const dialog = h('dialog', { id: 'game-properties', class: 'game-properties', 'aria-labelledby': 'game-properties-title' },
    h('header', {}, title, close), tabs, details, onStats ? h('button', { class: 'btn small secondary', type: 'button', onclick: () => { dialog.close(); onStats(); } }, 'Game history & stats') : null);
  viewport.append(dialog);
  dialog.addEventListener('click', e => {
    const rect = dialog.getBoundingClientRect();
    if (e.target === dialog && (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom)) dialog.close();
  });
  dialog.addEventListener('close', () => document.getElementById('game-players')?.focus({ preventScroll: true }));
  function owned(p) { return [...(p.properties || [])].sort((a, b) => a - b); }
  function renderDialog() {
    const p = state?.players.find(p => p.id === selected);
    if (!p) return;
    const ids = owned(p);
    title.textContent = `${p.name}'s cards`;
    const openSquare = details.querySelector('.property-deed[open]')?.dataset.square;
    const showMoney = details.querySelector('.player-money')?.open ?? true;
    tabs.replaceChildren(...state.players.map(other => h('button', {
      type: 'button', class: 'btn small secondary', 'aria-pressed': String(other.id === p.id), 'data-player': other.id,
      onclick: () => { selected = other.id; renderDialog(); tabs.querySelector(`[data-player="${other.id}"]`).focus(); }
    }, other.name)));
    details.replaceChildren(
      h('p', { class: 'game-property-summary' }, `${p.balance} gold · ${ids.length} property ${ids.length === 1 ? 'card' : 'cards'}${p.bankrupt ? ' · Out of the game' : ''}`),
      renderPropertyCards(board, p, { openSquare, showMoney })
    );
  }
  return {
    strip,
    open(id = state?.current_player) {
      if (!state || state.phase === 'LOBBY') return;
      selected = id || state.players[0].id;
      renderDialog();
      if (!dialog.open) dialog.showModal();
    },
    update(next) {
      const key = JSON.stringify([next.turn, next.phase, next.pending]);
      if (dialog.open && context !== key) dialog.close();
      context = key; state = next;
      strip.hidden = state.phase === 'LOBBY';
      strip.replaceChildren(...state.players.map(p => h('button', {
        type: 'button', class: `game-player-chip${p.id === state.current_player ? ' is-current' : ''}${p.bankrupt ? ' is-out' : ''}`,
        'aria-label': `${p.name}: ${p.balance} gold, ${owned(p).length} owned places${p.id === state.current_player ? ', current turn' : ''}. Browse places.`,
        onclick: () => this.open(p.id)
      }, tokenDot(p), h('span', {}, h('strong', {}, p.name), h('span', {}, p.bankrupt ? 'Out' : `${p.balance} gold`)),
        h('span', { class: 'game-player-count', 'aria-hidden': 'true' }, String(owned(p).length)))));
      if (dialog.open) renderDialog();
    }
  };
}
