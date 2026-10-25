// A read-only illustration of player.balance, not a separate cash inventory.
// Denominations come from design/Game design.pdf.
import { h } from '/static/net.js';
import { lineIcon } from '/static/icons.js';

const NOTES = [
  [500, '#d5c4dc'], [100, '#bed0b6'], [50, '#e5d39e'], [20, '#c9d8c3'],
  [10, '#cad5e1'], [5, '#e2c8c4'], [1, '#e6dfc7']
];

export function banknoteBreakdown(balance) {
  if (!Number.isSafeInteger(balance) || balance < 0) return [];
  let remaining = balance;
  return NOTES.flatMap(([value, color]) => {
    const count = Math.floor(remaining / value);
    remaining %= value;
    return count ? [{ value, count, color }] : [];
  });
}

export function renderPlayerMoney(player, { open = true } = {}) {
  const money = h('details', { class: 'player-money', open, 'data-player': player.id },
    h('summary', {}, h('span', { class: 'money-when-closed' }, 'Show money'), h('span', { class: 'money-when-open' }, 'Hide money')));
  const notes = banknoteBreakdown(player.balance);
  money.append(h('div', { class: 'money-wallet' },
    h('div', { class: 'money-wallet-heading' }, h('h3', {}, `${player.name}’s gold`),
      h('strong', { class: 'money-wallet-total' }, `${player.balance.toLocaleString()} gold`)),
    notes.length ? h('ul', { class: 'money-notes', 'aria-label': 'Gold shown as banknotes' }, ...notes.map(({ value, count, color }) =>
      h('li', { class: `money-note-stack${count > 1 ? ' has-stack' : ''}${count > 2 ? ' has-many' : ''}`, 'data-value': value, 'data-count': count, style: `--note-paper:${color}` },
        h('div', { class: 'gold-banknote', 'aria-hidden': 'true' },
          h('span', { class: 'money-note-brand' }, 'Storybook gold'),
          h('span', { class: 'money-note-value first' }, String(value)),
          h('span', { class: 'money-note-seal' }, lineIcon('tower'), h('small', {}, 'GOLD')),
          h('span', { class: 'money-note-value last' }, String(value))),
        h('span', { class: 'money-note-caption' }, `${value} gold · ${count} ${count === 1 ? 'note' : 'notes'}`))))
      : h('p', { class: 'money-empty' }, player.balance === 0 ? 'No gold remaining.' : 'Your balance is shown above.'),
    h('p', { class: 'money-wallet-note' }, 'An illustrated view of the balance.')));
  return money;
}
