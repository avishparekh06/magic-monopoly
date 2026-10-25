// Read-only title deeds. Artwork follows the design PDFs; values follow live game data.
import { h } from '/static/net.js';
import { renderPlayerMoney } from '/static/player-money.js';

const TALES = {
  brown: 'Hansel and Gretel', light_blue: 'Cinderella', pink: 'Sleeping Beauty',
  orange: 'The Three Little Pigs', red: 'Little Red Riding Hood',
  yellow: 'Beauty and the Beast', green: 'Peter Pan', dark_blue: 'Snow White'
};
let galleryId = 0;
const gold = value => `${value} gold`;
const ownable = square => ['property', 'railroad', 'utility'].includes(square?.type);

export function propertyRent(board, square, owned) {
  if (square.type === 'property') {
    const complete = board.groups[square.group].squares.every(id => owned.has(id));
    return { value: gold(square.rent * (complete ? 2 : 1)), complete };
  }
  const count = board.squares.filter(s => s.type === square.type && owned.has(s.id)).length;
  return square.type === 'railroad'
    ? { value: gold(board.railroad_rent[count - 1]), count }
    : { value: `${board.utility_multiplier[count - 1]} per dice point`, count };
}

export function renderPropertyCards(board, player, { openSquare, showMoney = true } = {}) {
  const owned = new Set(player.properties || []), groupName = `property-hand-${++galleryId}`;
  const gallery = h('div', { class: 'property-hand' }, renderPlayerMoney(player, { open: showMoney }));
  const groups = new Map();
  for (const square of board.squares.filter(s => ownable(s) && owned.has(s.id))) {
    const key = square.group || square.type;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(square);
  }
  const entries = [...groups].sort(([a], [b]) => {
    const order = [...Object.keys(board.groups), 'railroad', 'utility'];
    return order.indexOf(a) - order.indexOf(b);
  });
  const list = h('ul', { class: 'property-grid', 'aria-label': 'Owned property cards' });
  if (entries.length) gallery.append(list);
  const row = (label, value) => h('div', {}, h('dt', {}, label), h('dd', {}, value));
  for (const [key, squares] of entries) {
    const members = board.groups[key]?.squares || board.squares.filter(s => s.type === key).map(s => s.id);
    const count = members.filter(id => owned.has(id)).length;
    const color = board.groups[key]?.color || (key === 'railroad' ? '#a78350' : '#719994');
    const label = TALES[key] || (key === 'railroad' ? 'Enchanted workshops' : 'Wish-granting places');
    const progress = () => h('span', { class: 'property-set-progress', role: 'img', 'aria-label': `${count} of ${members.length} places owned` },
      ...members.map(id => h('span', { class: owned.has(id) ? 'is-owned' : '', 'aria-hidden': 'true' })));
    for (const square of squares) {
      const rent = propertyRent(board, square, owned);
      const breakdown = square.type === 'property'
        ? h('dl', { class: 'property-deed-facts' }, row('Purchase price', gold(square.price)), row('Base rent', gold(square.rent)),
          row('Rent with your collection', rent.value), row('Your tale', `${count} of ${members.length} places`))
        : h('dl', { class: 'property-deed-facts' }, row('Purchase price', gold(square.price)),
          row(square.type === 'railroad' ? 'Workshops owned' : 'Wishing places owned', `${count} of ${members.length}`),
          row('Rent with your collection', rent.value));
      const rates = square.type === 'property'
        ? h('p', {}, rent.complete ? 'You own the complete tale. Rent is doubled.' : 'Own every place in this tale to double its base rent.')
        : h('div', { class: 'property-rent-schedule' }, h('h4', {}, square.type === 'railroad' ? 'Workshop rents' : 'Wish rents'),
          h('ul', {}, ...(square.type === 'railroad' ? board.railroad_rent : board.utility_multiplier).map((rate, i) =>
            h('li', { class: i + 1 === count ? 'is-current' : '' }, `${i + 1} owned`, h('strong', {}, square.type === 'railroad' ? gold(rate) : `${rate} gold per dice point`)))));
      const front = h('summary', { class: 'property-deed-front' },
        h('span', { class: 'property-deed-band', 'aria-hidden': 'true' }),
        h('img', { class: 'property-deed-art', src: `/static/art/properties/${square.id}.svg`, alt: '', loading: 'lazy', decoding: 'async', width: 320, height: 200 }),
        h('span', { class: 'property-deed-title' }, square.name),
        h('span', { class: 'property-deed-kind' }, label),
        h('span', { class: 'property-deed-values' },
          h('span', {}, h('small', {}, 'Price'), h('strong', {}, gold(square.price))),
          h('span', {}, h('small', {}, 'Rent'), h('strong', {}, rent.value))),
        h('span', { class: 'property-deed-collection' }, progress(), key in board.groups && count === members.length ? 'Complete tale' : `${count}/${members.length} collected`),
        h('span', { class: 'property-deed-toggle' }, h('span', { class: 'when-closed' }, 'Details'), h('span', { class: 'when-open' }, 'Close')));
      const card = h('details', { class: 'property-deed', name: groupName, open: String(square.id) === String(openSquare), 'data-square': square.id, 'data-set': key, style: `--deed-color:${color}` },
        front, h('div', { class: 'property-deed-back' }, h('h4', {}, square.name), breakdown, rates));
      list.append(h('li', { class: 'property-card-slot' }, card));
      // Keep one enlarged card open even in browsers without details[name].
      card.addEventListener('toggle', () => {
        if (card.open) gallery.querySelectorAll('.property-deed[open]').forEach(other => { if (other !== card) other.open = false; });
      });
    }

  }
  if (!entries.length) gallery.append(h('div', { class: 'property-empty' }, h('h3', {}, 'No property cards yet'), h('p', {}, 'Buy a place and its illustrated deed will appear here.')));
  if (player.jail_free_cards > 0) gallery.append(h('section', { class: 'property-escape' },
    h('img', { src: '/static/art/properties/13.svg', alt: '', width: 120, height: 75, loading: 'lazy' }),
    h('div', {}, h('h3', {}, "Escape Rapunzel’s Tower"), h('p', {}, `${player.jail_free_cards} escape ${player.jail_free_cards === 1 ? 'card' : 'cards'} held · used automatically when needed.`))));
  return gallery;
}
