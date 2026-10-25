// Saved sessions and account statistics. Game outcomes come from recorded events.
import { h } from '/static/net.js';
import { avatarSvg } from '/static/avatars.js';

// Version the shared component stylesheet so an older cached page cannot render new markup unstyled.
const saveStyles = '/static/session-hub.css?v=save-cards-3';
if (![...document.querySelectorAll('link[rel="stylesheet"]')].some(link => link.getAttribute('href') === saveStyles)) {
  const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = saveStyles;
  document.head.append(link);
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) { const error = Error(typeof data.detail === 'string' ? data.detail : 'Please check the entered details.'); error.status = response.status; throw error; }
  return data;
}
const button = (text, onclick, extra = '') => h('button', { type: 'button', class: `btn small ${extra}`, onclick }, text);
const date = value => new Date(value * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
const metric = (label, value, hint) => h('div', { class: 'hub-metric' }, h('span', {}, label), h('strong', {}, value ?? '—'), hint ? h('small', {}, hint) : null);
const favorite = properties => properties?.length ? `${properties.map(p => p.name).join(' / ')} · ${properties[0].count} purchase${properties[0].count === 1 ? '' : 's'}${properties.length > 1 ? ' each (tied)' : ''}` : 'No purchases yet';

export function mountSessionHub({ parent = document.body, onNewGame } = {}) {
  let user = null, generation = 0, catalog = [], board = null, cards = null;
  let ownIds = new Set(), historyFilter = 'all', historyScope = 'mine';
  const title = h('h2', { id: 'session-hub-title' });
  const body = h('div', { class: 'hub-body' });
  const back = button('Back', () => {}, 'secondary hub-back'); back.hidden = true;
  const dialog = h('dialog', { id: 'session-hub', class: 'session-hub', 'aria-labelledby': 'session-hub-title' },
    h('header', { class: 'hub-header' }, h('div', { class: 'hub-heading' }, back, title), button('Close', () => dialog.close(), 'secondary')), body);
  parent.append(dialog);
  dialog.addEventListener('click', event => {
    const r = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) dialog.close();
  });
  function show(label, ...nodes) {
    generation++; dialog.dataset.screen = 'standard'; back.hidden = true; title.textContent = label; body.replaceChildren(...nodes); dialog.scrollTop = 0;
    if (!dialog.open) dialog.showModal();
    return generation;
  }
  const fresh = ticket => generation === ticket && dialog.open;
  const error = message => h('p', { class: 'hub-error', role: 'alert' }, message);
  function changed(account) { user = account; window.dispatchEvent(new CustomEvent('accountchange', { detail: account })); }
  const ready = api('/api/auth/me').then(data => changed(data.account)).catch(() => changed(null));
  async function syncUser() {
    await ready;
    try {
      const data = await api('/api/auth/me');
      if (data.account?.id !== user?.id || data.account?.display_name !== user?.display_name) changed(data.account);
    } catch { /* A failed write/read still reports its own connection error. */ }
    return user;
  }
  function goBack(label, action) { back.textContent = label; back.onclick = action; back.hidden = false; }
  function tabs(active) {
    const nav = h('div', { class: 'hub-tabs', role: 'tablist', 'aria-label': 'History and statistics' });
    for (const [key, label, open] of [['games', 'Games', () => hub.openHistory()], ['stats', 'Overall stats', () => hub.openProfile()]]) {
      nav.append(h('button', { type: 'button', role: 'tab', id: `hub-tab-${key}`, 'aria-selected': String(key === active),
        'aria-controls': 'hub-panel', tabindex: key === active ? 0 : -1, onclick: open }, label));
    }
    nav.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const items = [...nav.children], index = items.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowLeft' ? -1 : 1) + items.length) % items.length;
      items[next].focus(); items[next].click();
    });
    return nav;
  }
  function section(active, ...nodes) {
    const focusTab = body.querySelector('[role="tab"]')?.contains(document.activeElement) || document.activeElement?.getAttribute('role') === 'tab';
    body.replaceChildren(tabs(active), h('div', { id: 'hub-panel', role: 'tabpanel', 'aria-labelledby': `hub-tab-${active}` }, ...nodes));
    if (focusTab) body.querySelector('[aria-selected="true"]').focus();
  }
  const disclosure = (label, ...nodes) => h('details', { class: 'hub-details' }, h('summary', {}, label), ...nodes);
  async function loadCatalog() {
    await syncUser();
    const publicGames = await api('/api/games');
    let own = [];
    if (user) { try { own = (await api('/api/account/games')).games; } catch(e) { if (e.status === 401) changed(null); else throw e; } }
    ownIds = new Set(own.map(game => game.game_id));
    const merged = new Map(publicGames.map(game => [game.game_id, game]));
    for (const game of own) merged.set(game.game_id, { ...merged.get(game.game_id), ...game });
    catalog = [...merged.values()].sort((a,b) => b.updated_at-a.updated_at);
    return catalog;
  }
  function resumeUrl(game) {
    if (game.status === 'over') return null;
    if (game.resume_url) return game.resume_url;
    if (game.host_url) return game.phase === 'LOBBY' ? game.host_url : game.board_url;
    return null;
  }
  function contextFor(game, context = {}) {
    const result = { ...context };
    if (game?.host_url) result.host = new URL(game.host_url, location.origin).searchParams.get('host');
    if (game?.player_id) result.player = game.player_id;
    if (game?.resume_url?.includes('/join/')) result.seat = game.resume_url.split('/').at(-1);
    return result;
  }
  function sessionCard(game, options = {}) {
    const names = game.players.map(p => p.name).join(' · ');
    const resume = resumeUrl(game), personal = game.stats;
    const winner = game.players.find(p => p.id === game.winner)?.name;
    const finished = game.status === 'over', waiting = game.phase === 'LOBBY';
    const status = finished ? 'Finished' : waiting ? 'In lobby' : 'In progress';
    const result = finished ? (winner ? `${winner} won` : 'Result not recorded') : waiting ? 'Ready when you are' : `Turn ${game.turn}`;
    const contents = [
      h('span', { class: 'hub-session-main' }, h('strong', {}, names),
        h('span', { class: 'hub-subtle' }, date(game.updated_at)),
        h('span', { class: 'hub-session-result' }, result,
          personal?.placement != null ? ` · Your finish: #${personal.placement}` : personal ? ` · Playing as ${personal.name}` : '')),
      h('span', { class: 'hub-session-end' }, h('span', { class: `hub-status ${finished ? 'finished' : ''}` }, status),
        h('span', { class: 'hub-row-action' }, options.continue ? 'Continue' : 'View game'))
    ];
    const open = () => {
      const scroll = dialog.scrollTop;
      hub.openGame(game, options.context, async () => {
        await hub.openHistory();
        if (dialog.open && body.querySelector('#hub-tab-games[aria-selected="true"]')) {
          dialog.scrollTop = scroll;
          [...body.querySelectorAll('[data-game]')].find(row => row.dataset.game === game.game_id)?.querySelector('button')?.focus({ preventScroll: true });
        }
      });
    };
    const action = options.continue && resume
      ? h('a', { class: 'hub-session-row', href: resume }, ...contents)
      : h('button', { type: 'button', class: 'hub-session-row', onclick: open }, ...contents);
    return h('article', { class: 'hub-session', 'data-game': game.game_id }, action);
  }
  function showCatalog(games, mode) {
    const list = h('div', { class: 'hub-session-list' });
    const filtered = mode === 'continue' ? games.filter(g => g.status !== 'over' && g.status !== 'abandoned' && resumeUrl(g)) : games;
    if (!filtered.length) list.append(h('div', { class: 'hub-empty' }, h('h3', {}, mode === 'continue' ? 'A new story awaits' : 'No games here yet'),
      h('p', {}, mode === 'continue' ? 'Start a new game. Your unfinished games will appear here next time.' : historyFilter !== 'all' ? 'Try a different filter to see your other games.' : user ? 'Your linked games appear here. Choose All available games to find an older session.' : 'Games appear here as you play. Sign in to keep your own results together.')));
    else list.append(...filtered.map(game => sessionCard(game, { continue: mode === 'continue' })));
    return list;
  }
  function continuePicker(games) {
    const saves = games.filter(game => !['over', 'abandoned'].includes(game.status) && resumeUrl(game));
    const grid = h('div', { class: 'hub-save-grid', 'aria-label': 'Saved games' });
    saves.forEach((game, index) => {
      const waiting = game.phase === 'LOBBY';
      const portraits = h('span', { class: 'hub-slot-portraits', 'aria-hidden': 'true' });
      game.players.forEach(player => {
        const portrait = h('span', { class: 'hub-slot-portrait' });
        const svg = avatarSvg(player.id);
        if (svg) {
          portrait.innerHTML = svg;
          portrait.firstElementChild.setAttribute('width', '35');
          portrait.firstElementChild.setAttribute('height', '39');
        } else portrait.textContent = player.name.slice(0, 1);
        portraits.append(portrait);
      });
      grid.append(h('a', { class: 'hub-save-slot', href: resumeUrl(game), 'data-game': game.game_id },
        h('span', { class: 'hub-slot-art' }, h('span', { class: 'hub-slot-number', 'aria-label': `Save ${index + 1}` }, String(index + 1)), portraits),
        h('span', { class: 'hub-slot-content' },
          h('span', { class: 'hub-slot-label' }, index === 0 ? 'Most recent' : 'Saved game'),
          h('strong', { class: 'hub-slot-names' }, game.players.map(player => player.name).join(' & ')),
          h('span', { class: 'hub-slot-turn' }, waiting ? 'In lobby' : `Turn ${game.turn}`),
          h('time', { datetime: new Date(game.updated_at * 1000).toISOString() }, date(game.updated_at)),
          h('span', { class: 'hub-slot-action' }, waiting ? 'Continue lobby' : 'Continue game'))));
    });
    // Empty placeholders match the save-slot layout; they never replace or limit real saved games.
    for (let index = saves.length; index < 3; index++) {
      grid.append(h('div', { class: 'hub-save-slot is-empty', 'aria-label': `Save ${index + 1}: empty` },
        h('span', { class: 'hub-slot-art', 'aria-hidden': 'true' }, h('span', { class: 'hub-slot-number' }, String(index + 1))),
        h('span', { class: 'hub-slot-content' }, h('strong', { class: 'hub-slot-names' }, 'Empty'),
          h('span', { class: 'hub-slot-empty-note' }, 'A new story starts here'))));
    }
    grid.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      const slots = [...grid.querySelectorAll('a.hub-save-slot')], index = slots.indexOf(document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      const columns = getComputedStyle(grid).gridTemplateColumns.split(' ').length;
      const offset = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : event.key === 'ArrowUp' ? -columns : columns;
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? slots.length - 1 : (index + offset + slots.length) % slots.length;
      slots[next].focus();
    });
    return grid;
  }
  function profileMetrics(stats) {
    return [
      h('div', { class: 'hub-metrics hub-highlights' }, metric('Games played', stats.games_played), metric('Wins', stats.wins),
        metric('Win rate', stats.win_rate == null ? '—' : `${stats.win_rate}%`),
        metric('Average finish', stats.average_placement == null ? '—' : `#${stats.average_placement}`)),
      h('div', { class: 'hub-favorite' }, h('h3', {}, 'Most bought property'), h('p', {}, favorite(stats.most_bought_properties))),
      disclosure('More stats', h('div', { class: 'hub-metrics' }, metric('Games completed', stats.completed_games), metric('Properties bought', stats.bought),
        metric('Rent earned', `${stats.rent_received} gold`), metric('Rent paid', `${stats.rent_paid} gold`), metric('Average roll', stats.avg_roll), metric('Best roll', stats.best_roll)),
        h('p', { class: 'hub-subtle' }, `Win rate uses ${stats.result_games} games with recorded winners. Average finish uses ${stats.ranked_games} completed games with known results. Ongoing games contribute purchases and gold, not final results.`))
    ];
  }
  const hub = {
    dialog,
    async openPlay() {
      const ticket = show('Continue playing', h('p', {}, 'Loading saved games…'));
      dialog.dataset.screen = 'play';
      try {
        const games = await loadCatalog(); if (!fresh(ticket)) return;
        const available = games.filter(g => !user || ownIds.has(g.game_id) || g.host_url);
        body.replaceChildren(
          h('div', { class: 'hub-play-start' }, button('New game', () => { dialog.close(); onNewGame?.(user); })),
          h('p', { class: 'hub-play-intro' }, 'Choose a story to continue.'), continuePicker(available),
          user ? h('p', { class: 'hub-subtle' }, `Signed in as ${user.display_name}`) : button('Sign in to find your saved games', () => hub.openAuth(false, () => hub.openPlay()), 'hub-text-button'));

      } catch (e) { if (fresh(ticket)) body.replaceChildren(error(e.message), button('Try again', () => hub.openPlay(), 'secondary')); }
    },
    async openHistory() {
      const tabFocused = document.activeElement?.getAttribute('role') === 'tab';
      const ticket = show('History & stats', h('p', {}, 'Loading games…'));
      try {
        const games = await loadCatalog(); if (!fresh(ticket)) return;
        const list = h('div');
        const filter = h('select', { 'aria-label': 'Filter games', onchange: event => { historyFilter = event.target.value; drawList(); } },
          ...[['all', 'All games'], ['active', 'Unfinished'], ['over', 'Finished']].map(([value, label]) => h('option', { value, selected: historyFilter === value }, label)));
        const scope = user ? h('select', { 'aria-label': 'Game collection', onchange: event => { historyScope = event.target.value; drawList(); } },
          h('option', { value: 'mine', selected: historyScope === 'mine' }, 'My games'), h('option', { value: 'all', selected: historyScope === 'all' }, 'All available games')) : null;
        const count = h('p', { class: 'hub-subtle', role: 'status' });
        const intro = h('p', { class: 'hub-subtle' });
        function drawList() {
          const collection = user && historyScope === 'mine' ? games.filter(g => ownIds.has(g.game_id)) : games;
          const filtered = collection.filter(g => historyFilter === 'all' || (historyFilter === 'over' ? g.status === 'over' : g.status !== 'over'));
          intro.textContent = user ? `${historyScope === 'mine' ? user.display_name + '’s saved games.' : 'All available games.'} Open a game to see results or continue playing.` : 'Open a game to see results or continue playing. Sign in under Overall stats to track your results.';
          count.textContent = `${filtered.length} ${filtered.length === 1 ? 'game' : 'games'} · Most recent first`;
          list.replaceChildren(showCatalog(filtered, 'history'));
        }
        drawList();
        section('games', intro,
          h('div', { class: 'hub-list-toolbar' }, count, h('div', { class: 'hub-filters' }, scope, filter)), list);
        if (tabFocused) body.querySelector('#hub-tab-games').focus();
      } catch (e) { if (fresh(ticket)) section('games', error(e.message), button('Try again', () => hub.openHistory(), 'secondary')); }
    },
    async openProfile() {
      const tabFocused = document.activeElement?.getAttribute('role') === 'tab';
      const ticket = show('History & stats', h('p', {}, 'Loading your stats…'));
      try {
        await syncUser(); if (!fresh(ticket)) return;
        if (!user) {
          section('stats', h('div', { class: 'hub-empty' }, h('h3', {}, 'Your story, across every game'),
            h('p', {}, 'Sign in to see your wins, average finish and favorite properties. You can still play and view game history as a guest.'),
            button('Sign in', () => hub.openAuth(false, () => hub.openProfile())),
            button('Create an account', () => hub.openAuth(true, () => hub.openProfile()), 'hub-text-button')));
        } else {
          const data = await api('/api/account/stats'); if (!fresh(ticket)) return;
          section('stats', h('p', { class: 'hub-profile-name' }, data.account.display_name), ...profileMetrics(data.stats),
            h('p', { class: 'hub-subtle' }, data.stats.games_played ? 'Your linked player results across all games.' : 'No games played yet. Start a new game while signed in to save your results. To add an older game, open its player details and link your account.'),
            disclosure('Account', h('p', {}, `Signed in as ${data.account.username}`), button('Sign out', async () => {
              try { await api('/api/auth/logout', {}); changed(null); hub.openProfile(); } catch(e) { body.prepend(error(e.message)); }
            }, 'secondary')));
        }
        if (tabFocused) body.querySelector('#hub-tab-stats').focus();
      } catch (e) { if (fresh(ticket)) { if (e.status === 401) { changed(null); hub.openProfile(); } else section('stats', error(e.message), button('Try again', () => hub.openProfile(), 'secondary')); } }
    },
    openAuth(register = false, after = () => hub.openProfile()) {
      const username = h('input', { name: 'username', required: true, minlength: 3, maxlength: 32, autocomplete: 'username', placeholder: 'Username' });
      const password = h('input', { name: 'password', type: 'password', required: true, minlength: register ? 8 : 1, maxlength: 128, autocomplete: register ? 'new-password' : 'current-password' });
      const display = h('input', { name: 'display_name', maxlength: 40, placeholder: 'Name shown on your profile' });
      const repeat = h('input', { name: 'repeat', type: 'password', required: register, autocomplete: 'new-password' });
      const message = h('p', { class: 'hub-error', role: 'alert' });
      const submit = h('button', { class: 'btn', type: 'submit' }, register ? 'Create account' : 'Sign in');
      const ticket = show(register ? 'Create your player account' : 'Sign in to your player account',
        h('p', {}, 'Save your sessions, favorite purchases and results across games.'),
        h('form', { class: 'hub-form', onsubmit: async event => {
          event.preventDefault(); message.textContent = '';
          if (register && password.value !== repeat.value) { message.textContent = 'The passwords do not match.'; return; }
          submit.disabled = true;
          try {
            const data = await api(`/api/auth/${register ? 'register' : 'login'}`, { username: username.value, password: password.value, display_name: display.value });
            changed(data.account); if (fresh(ticket)) after();
          } catch(e) { message.textContent = e.message; submit.disabled = false; }
        } }, h('label', {}, 'Username', username), register ? h('label', {}, 'Display name', display) : null,
          h('label', {}, 'Password', password), register ? h('label', {}, 'Repeat password', repeat) : null, message, submit),
        button(register ? 'Already have an account? Sign in' : 'Create an account', () => hub.openAuth(!register, after), 'hub-text-button'));
      goBack('Back', after);
    },
    async linkSeat(gameId, playerId, context = {}, after = () => {}, onCancel = () => dialog.close()) {
      await syncUser();
      let roster = {};
      try { roster = (await api(`/api/games/${gameId}/accounts?${new URLSearchParams(context)}`)).players; } catch {}
      const old = roster[playerId];
      let create = false;
      const username = h('input', { name: 'username', required: true, maxlength: 32, autocomplete: 'username' });
      const password = h('input', { name: 'password', type: 'password', required: true, maxlength: 128, autocomplete: 'current-password' });
      const display = h('input', { name: 'display_name', maxlength: 40, placeholder: 'Display name for a new account' });
      const createCheck = h('input', { type: 'checkbox', onchange: event => {
        create = event.target.checked; display.parentElement.hidden = !create; password.autocomplete = create ? 'new-password' : 'current-password';
      } });
      const message = h('p', { class: 'hub-error', role: 'alert' });
      const finish = data => {
        const name = data.account.username;
        show('Stats account linked', h('p', {}, `This player’s recorded stats now belong to ${name}. Earlier recorded events in this session are included.`),
          h('p', { class: 'hub-subtle' }, 'Linking another player does not change the account signed in on this device.'),
          button('Done', () => { dialog.close(); after(data.account); }));
      };
      const submit = h('button', { type: 'submit', class: 'btn' }, 'Link this account');
      const ticket = show('Save this player’s stats', old ? h('p', {}, `Currently linked: ${old.username}`) : h('p', {}, 'Choose the account for this player. Different players need separate accounts.'),
        user ? button(`Use my account · ${user.username}`, async () => {
          try { const data = await api(`/api/games/${gameId}/players/${playerId}/account`, { ...context, use_current: true, expected_account: user.id }); if (fresh(ticket)) finish(data); }
          catch(e) { message.textContent = e.message; }
        }) : button('Sign in to use your account', () => hub.openAuth(false, () => hub.linkSeat(gameId, playerId, context, after, onCancel)), 'secondary'),
        h('form', { class: 'hub-form', onsubmit: async event => {
          event.preventDefault(); submit.disabled = true; message.textContent = '';
          try {
            const data = await api(`/api/games/${gameId}/players/${playerId}/account`, { ...context, username: username.value, password: password.value, display_name: display.value, create });
            if (fresh(ticket)) finish(data);
          } catch(e) { message.textContent = e.message; submit.disabled = false; }
        } }, h('h3', {}, user ? 'Or link another player’s account' : 'Account details'), h('label', {}, 'Username', username), h('label', {}, 'Password', password),
          h('label', { class: 'hub-check' }, createCheck, 'Create this as a new account'), h('label', { hidden: true }, 'Display name', display), message, submit));
      goBack('Back to game', onCancel);
    },
    async openGame(gameOrId, context = {}, returnTo = () => hub.openHistory()) {
      const ticket = show('Game details', h('p', {}, 'Loading this game…'));
      goBack('Back to games', returnTo);
      try {
        const games = await loadCatalog();
        const id = typeof gameOrId === 'string' ? gameOrId : gameOrId.game_id;
        const game = games.find(game => game.game_id === id) || (typeof gameOrId === 'string' ? null : gameOrId);
        const ctx = contextFor(game, context);
        const query = new URLSearchParams(Object.entries(ctx).filter(([key]) => key === 'host' || key === 'seat'));
        const [stats, history, accounts] = await Promise.all([
          api(`/api/games/${id}/stats`), api(`/api/games/${id}/history`),
          api(`/api/games/${id}/accounts?${query}`).catch(() => ({ players: {} }))]);
        if (!board) board = await api('/api/board');
        if (!cards) cards = await api('/api/cards');
        if (!fresh(ticket)) return;
        const names = new Map(stats.players.map(p => [p.id, p.name]));
        const winner = names.get(stats.winner);
        const header = h('div', { class: 'hub-game-summary' },
          h('p', { class: 'hub-game-names' }, stats.players.map(p => p.name).join(' · ')),
          h('p', {}, winner ? `${winner} won` : stats.phase === 'GAME_OVER' ? 'Finished · result not recorded' : stats.phase === 'LOBBY' ? 'Waiting for players' : `In progress${game ? ` · Turn ${game.turn}` : ''}`),
          game ? h('p', { class: 'hub-subtle' }, date(game.created_at)) : null,
          game && resumeUrl(game) ? h('a', { class: 'btn', href: resumeUrl(game) }, game.phase === 'LOBBY' ? 'Continue lobby' : 'Continue game') : null,
          // The host can end a game for good: it stops being resumed on restart and its links die; history stays.
          game && ctx.host && game.status !== 'over' && game.status !== 'abandoned'
            ? button('End this game', async () => {
                if (!confirm('End this game for everyone? Its history is kept, but it cannot be continued.')) return;
                try { await api(`/api/games/${id}/abandon?host=${encodeURIComponent(ctx.host)}`, {}); hub.openGame(id, ctx, returnTo); }
                catch (e) { alert(e.message); }
              }, 'secondary') : null);
        const playerCards = [...stats.players].sort((a,b) => stats.phase === 'GAME_OVER' ? (a.placement ?? Infinity) - (b.placement ?? Infinity) : 0).map(p => h('details', { class: 'hub-player-stats', 'data-player': p.id },
          h('summary', {}, h('span', { class: 'hub-player-name' }, h('strong', {}, p.name),
            h('span', { class: 'hub-subtle' }, p.placement != null ? `Finish #${p.placement}` : stats.phase === 'GAME_OVER' ? 'Finish not recorded' : stats.phase === 'LOBBY' ? 'Waiting to start' : p.bankrupt ? 'Out' : 'Still playing')),
            h('span', { class: 'hub-player-score' }, h('strong', {}, `${p.balance} gold`), h('span', { class: 'hub-subtle' }, `${p.bought} ${p.bought === 1 ? 'property' : 'properties'} bought`))),
          h('div', { class: 'hub-player-breakdown' }, h('div', { class: 'hub-metrics' }, metric('Rent earned', `${p.rent_received} gold`), metric('Rent paid', `${p.rent_paid} gold`), metric('Average roll', p.avg_roll), metric('Best roll', p.best_roll)),
            h('p', {}, 'Most bought: ', favorite(p.most_bought_properties)),
            disclosure('Stats account', h('p', { class: 'hub-subtle' }, accounts.players[p.id] ? `Linked to ${accounts.players[p.id].username}` : 'Guest player · link an account to include this game in overall stats.'),
              (ctx.host || ctx.player === p.id) ? button(accounts.players[p.id] ? 'Manage account link' : 'Link account',
                () => hub.linkSeat(id, p.id, ctx, () => hub.openGame(game || id, ctx, returnTo), () => hub.openGame(game || id, ctx, returnTo)), 'secondary') : null))));
        const timeline = [];
        const place = index => board.squares[index]?.name || 'a space';
        const person = id => names.get(id) || (id === 'bank' ? 'the bank' : 'a player');
        for (const row of history) {
          if (row.action.action === 'new_game') timeline.push({ time: row.ts, text: 'Game created' });
          for (const e of row.events) {
            let text = null;
            if (e.type === 'game_started') text = 'Game started';
            if (e.type === 'turn_started') text = `${person(e.player)}’s turn`;
            if (e.type === 'rolled') text = `${person(e.player)} rolled ${e.value}`;
            if (e.type === 'bought') text = `${person(e.player)} bought ${place(e.square)}`;
            if (e.type === 'paid') text = `${person(e.from)} paid ${e.amount} gold to ${person(e.to)}${e.reason === 'rent' ? ' in rent' : e.reason === 'buy' ? ' for a purchase' : ''}`;
            if (e.type === 'bankrupt') text = `${person(e.player)} went bankrupt`;
            if (e.type === 'game_over') text = `${person(e.winner)} won the game`;
            if (e.type === 'salary') text = `${person(e.player)} collected ${e.amount} gold for passing Once Upon a Time`;
            if (e.type === 'card_drawn') text = `${person(e.player)} drew ${cards.cards.find(card => card.id === e.id)?.title || 'a card'}`;
            if (e.type === 'jailed') text = `${person(e.player)} went to Rapunzel’s Tower`;
            if (text) timeline.push({ time: row.ts, text });
          }
        }
        const list = h('ol', { class: 'hub-timeline' }); let shown = 0;
        const more = button('Show older events', () => addOlder(), 'secondary');
        function addOlder() {
          const rows = timeline.slice(Math.max(0, timeline.length-shown-80), timeline.length-shown).reverse();
          list.append(...rows.map(item => h('li', {}, h('time', {}, date(item.time)), h('span', {}, item.text))));
          shown += rows.length; more.hidden = shown >= timeline.length;
        }
        addOlder();
        body.replaceChildren(header, h('h3', {}, 'Player results'), h('p', { class: 'hub-subtle' }, 'Select a player for their breakdown. Final standings are recorded when known.'), ...playerCards,
          disclosure(`Game activity · ${timeline.length} events`, h('p', { class: 'hub-subtle' }, `Most recent first · Session ${id}`), list, more));
      } catch(e) { if (fresh(ticket)) body.replaceChildren(error(e.message)); }
    }
  };
  return hub;
}
