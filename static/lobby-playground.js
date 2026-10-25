// A shared waiting-room toy. Coordinates are courtyard-only, never board positions.
import { h } from '/static/net.js';
import { characterSvg, characterName } from '/static/avatars.js';

export function mountLobbyPlayground(parent, send) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const root = h('section', { class: 'lobby-playground home-roaming', hidden: true, 'aria-label': 'Fairy-tale characters in the home landscape' });
  const instruction = h('p', { id: 'home-roam-help', class: 'lobby-instructions' }, 'Move to wander · Click or Space to jump');
  const arena = h('div', { class: 'lobby-arena', tabindex: '0', role: 'group', 'aria-label': 'Move your character around the home landscape. Use the cursor, touch, arrow keys or WASD. Click or press Space to jump.', 'aria-describedby': 'home-roam-help' });
  const label = h('span', { class: 'lobby-you-label' });
  const jumpButton = h('button', { class: 'btn small lobby-jump', type: 'button', onclick: () => jump() }, 'Jump');
  root.append(arena, h('div', { class: 'lobby-play-controls' }, h('div', {}, label, instruction), jumpButton));
  parent.append(root);
  let state = null, me = null, online = false, active = false, raf = null, lastFrame = 0;
  let lastInput = 0, lastSent = 0, timer = null, lastJump = -Infinity, pendingJump = false;
  const actors = new Map(), poses = new Map();
  const clamp = (v, low = .08, high = .92) => Math.max(low, Math.min(high, v));
  function spawn(p, index) {
    const pose = poses.get(p.id) || { x: .25 + index * .25, y: .62, jump: 0 };
    const figure = h('div', { class: 'lobby-figure', 'aria-hidden': 'true' });
    figure.innerHTML = characterSvg(p.id);
    const el = h('div', { class: 'lobby-character', 'data-player': p.id, 'data-character': characterName(p.id), title: characterName(p.id) }, h('div', { class: 'lobby-character-shadow' }), figure,
      h('span', { class: 'lobby-character-name' }, p.name));
    arena.append(el);
    const actor = { el, figure, shadow: el.firstChild, x: pose.x, y: pose.y, tx: pose.x, ty: pose.y, jump: pose.jump, jumpAt: -Infinity };
    actors.set(p.id, actor);
    if (!reduced.matches) figure.animate([{ opacity: 0, transform: 'translateY(-45px) scale(.6)' }, { opacity: 1, transform: 'none' }], { duration: 420, easing: 'cubic-bezier(.2,.8,.3,1)' });
  }
  function run(now) {
    raf = null;
    if (!active || document.hidden) return;
    const dt = Math.min((now - lastFrame) / 1000 || .016, .05); lastFrame = now;
    const width = arena.clientWidth, height = arena.clientHeight;
    for (const [id, actor] of actors) {
      const dx = actor.tx - actor.x, dy = actor.ty - actor.y, distance = Math.hypot(dx, dy);
      const speed = reduced.matches ? 1 : Math.min(1, dt * 7);
      actor.x += dx * speed; actor.y += dy * speed;
      const scale = .94 + actor.y * .12;
      const x = 50 + actor.x * Math.max(1, width - 100), y = height - 115 + actor.y * 35;
      actor.el.style.left = `${x}px`; actor.el.style.top = `${y}px`;
      actor.el.style.zIndex = String(Math.round(y));
      actor.el.style.setProperty('--character-scale', scale);
      actor.el.classList.toggle('is-you', id === me);
      const progress = (now - actor.jumpAt) / 620;
      const leap = progress >= 0 && progress < 1 ? Math.sin(progress * Math.PI) * (reduced.matches ? 8 : 60) : 0;
      const step = distance > .003 && !reduced.matches ? Math.sin(now / 65) * 3 : 0;
      actor.figure.style.transform = `translateY(${-leap - Math.abs(step)}px) rotate(${step}deg)`;
      actor.shadow.style.transform = `scale(${1 - leap / 150})`;
      actor.el.dataset.jumping = String(progress >= 0 && progress < 1);
    }
    raf = requestAnimationFrame(run);
  }
  function schedule() { if (active && !document.hidden && raf === null) { lastFrame = performance.now(); raf = requestAnimationFrame(run); } }
  function sendPosition(jumping = false) {
    if (!active || !online || !actors.has(me)) return;
    clearTimeout(timer); timer = null;
    const now = performance.now(), actor = actors.get(me);
    if (!jumping && now - lastSent < 80) { timer = setTimeout(() => sendPosition(), 80 - (now - lastSent)); return; }
    lastSent = now;
    send({ type: 'lobby_move', x: actor.tx, y: actor.ty, jump: jumping });
  }
  function move(x, y) {
    const actor = actors.get(me);
    if (!active || !online || !actor) return;
    actor.tx = clamp(x, 0, 1); actor.ty = clamp(y, 0, 1); lastInput = performance.now(); sendPosition();
  }
  function jump() {
    const now = performance.now(), actor = actors.get(me);
    if (!active || !online || !actor || now - lastJump < 650) return;
    lastJump = now; actor.jumpAt = now; pendingJump = true; sendPosition(true);
  }
  const pointerMove = e => {
    if (e.target.closest('button') || (e.pointerType !== 'mouse' && !e.buttons)) return;
    const rect = arena.getBoundingClientRect();
    move((e.clientX - rect.left - 50) / Math.max(1, rect.width - 100), (e.clientY - rect.top - (rect.height - 115)) / 35);
  };
  arena.addEventListener('pointermove', pointerMove);
  arena.addEventListener('pointerdown', e => { if (e.target.closest('button')) return; arena.focus({ preventScroll: true }); arena.setPointerCapture(e.pointerId); pointerMove(e); });
  arena.addEventListener('click', e => { if (!e.target.closest('button')) jump(); });
  arena.addEventListener('keydown', e => {
    const actor = actors.get(me); if (!actor || e.target !== arena) return;
    const direction = { ArrowLeft: [-1, 0], a: [-1, 0], ArrowRight: [1, 0], d: [1, 0], ArrowUp: [0, -1], w: [0, -1], ArrowDown: [0, 1], s: [0, 1] }[e.key];
    if (direction) { e.preventDefault(); move(actor.tx + direction[0] * .07, actor.ty + direction[1] * .1); }
    else if (e.code === 'Space') { e.preventDefault(); if (!e.repeat) jump(); }
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { cancelAnimationFrame(raf); raf = null; clearTimeout(timer); timer = null; }
    else schedule();
  });
  new ResizeObserver(schedule).observe(arena);
  function stop() { cancelAnimationFrame(raf); raf = null; clearTimeout(timer); timer = null; }
  return {
    update(next, identity) {
      if (state?.game_id !== next.game_id || (identity && identity !== me)) {
        for (const actor of actors.values()) actor.el.remove();
        actors.clear(); poses.clear(); pendingJump = false; lastJump = -Infinity; lastInput = 0;
      }
      state = next; me = identity || next.players[0]?.id;
      active = next.phase === 'LOBBY'; root.hidden = !active;
      if (!active) { stop(); return; }
      next.players.forEach((p, i) => {
        if (p.connected && !actors.has(p.id)) spawn(p, i);
        if (actors.has(p.id)) actors.get(p.id).el.querySelector('.lobby-character-name').textContent = p.name;
        if (!p.connected && actors.has(p.id)) { actors.get(p.id).el.remove(); actors.delete(p.id); }
      });
      const mine = next.players.find(p => p.id === me);
      label.textContent = online ? (mine?.name === characterName(me) ? `${characterName(me)} · You` : `${mine?.name || 'You'} · ${characterName(me)}`) : 'Connecting…';
      instruction.textContent = matchMedia('(pointer: coarse)').matches ? 'Drag to wander · Tap Jump to hop' : 'Move to wander · Click or Space to jump';
      jumpButton.disabled = !online;
      schedule();
    },
    event(message) {
      if (!active) return;
      const entries = message.type === 'lobby_presence' ? Object.entries(message.players) : message.type === 'lobby_motion' ? [[message.player, message]] : [];
      for (const [id, pose] of entries) {
        poses.set(id, pose);
        const actor = actors.get(id); if (!actor) continue;
        if (message.type === 'lobby_presence' || id !== me || performance.now() - lastInput > 250) { actor.tx = clamp(pose.x, 0, 1); actor.ty = clamp(pose.y, 0, 1); }
        if (pose.jump > actor.jump && message.type !== 'lobby_presence') {
          if (id === me && pendingJump) pendingJump = false;
          else actor.jumpAt = performance.now();
        }
        actor.jump = pose.jump;
      }
    },
    connection(status) { online = status === 'online'; if (status === 'closed') { active = false; root.hidden = true; jumpButton.disabled = true; stop(); } if (!online) { clearTimeout(timer); timer = null; pendingJump = false; } if (state && status !== 'closed') this.update(state, me); }
  };
}
