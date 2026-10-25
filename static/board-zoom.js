// Shared 2D map magnification; it changes presentation, never game state.
import { h } from '/static/net.js';

export function mountMapZoom(board, viewport, controls) {
  let zoom = 1;
  const minus = h('button', { type: 'button', class: 'btn small secondary', 'aria-label': 'Zoom out on 2D board' }, '−');
  const fit = h('button', { type: 'button', class: 'btn small secondary', title: 'Reset to full board', 'aria-label': 'Reset 2D zoom to fit board' }, '100%');
  const plus = h('button', { type: 'button', class: 'btn small secondary', 'aria-label': 'Zoom in on 2D board' }, '+');
  const group = h('div', { class: 'map-zoom-controls', role: 'group', 'aria-label': '2D board zoom' }, minus, fit, plus);
  controls.append(group);
  function updatePanSpace() {
    const width = viewport.clientWidth, height = viewport.clientHeight;
    const size = Math.min(width, height);
    viewport.style.setProperty('--map-pan-x', `${(width - size) / 2}px`);
    viewport.style.setProperty('--map-pan-y', `${(height - size) / 2}px`);
  }
  new ResizeObserver(updatePanSpace).observe(viewport);
  function sync() {
    updatePanSpace();
    group.hidden = viewport.dataset.render === '3d';
    minus.disabled = zoom <= 1;
    plus.disabled = zoom >= 2.5;
    fit.textContent = zoom === 1 ? 'Zoom' : `${Math.round(zoom * 100)}%`;
    viewport.dataset.mapZoom = String(zoom);
    viewport.style.setProperty('--map-zoom', zoom);
    viewport.setAttribute('aria-label', zoom === 1 ? 'Game board' : 'Zoomed game board. Drag to look around.');
  }
  function setZoom(next) {
    const surface = viewport.getBoundingClientRect(), map = board.getBoundingClientRect();
    const x = (surface.left + viewport.clientWidth / 2 - map.left) / map.width;
    const y = (surface.top + viewport.clientHeight / 2 - map.top) / map.height;
    zoom = Math.max(1, Math.min(2.5, next));
    sync();
    requestAnimationFrame(() => {
      viewport.scrollLeft = zoom === 1 ? 0 : board.offsetLeft + x * board.offsetWidth - viewport.clientWidth / 2;
      viewport.scrollTop = zoom === 1 ? 0 : board.offsetTop + y * board.offsetHeight - viewport.clientHeight / 2;
    });
  }
  // Mouse, pen, and touch all pan the map directly. Only the map starts
  // a drag, so cards, dialog controls, and game actions retain their clicks.
  let pan = null;
  let suppressClick = false;
  viewport.addEventListener('pointerdown', event => {
    if (zoom <= 1 || viewport.dataset.render === '3d' || pan || !event.isPrimary || event.button !== 0
        || (!board.contains(event.target) && event.target !== viewport) || event.target.closest('button, a, input, select, textarea, [role="button"]')) return;
    pan = { id: event.pointerId, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop, moved: false };
    viewport.setPointerCapture(event.pointerId);
    viewport.classList.add('map-panning');
    event.preventDefault();
  });
  viewport.addEventListener('dragstart', event => { if (zoom > 1 && board.contains(event.target)) event.preventDefault(); });
  viewport.addEventListener('pointermove', event => {
    if (!pan || event.pointerId !== pan.id) return;
    const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
    if (Math.hypot(dx, dy) > 4) pan.moved = true;
    viewport.scrollLeft = pan.left - dx;
    viewport.scrollTop = pan.top - dy;
    event.preventDefault();
  });
  function stopPan(event) {
    if (!pan || event.pointerId !== pan.id) return;
    suppressClick = pan.moved;
    pan = null;
    viewport.classList.remove('map-panning');
    if (viewport.hasPointerCapture(event.pointerId)) viewport.releasePointerCapture(event.pointerId);
    setTimeout(() => { suppressClick = false; }, 0);
  }
  viewport.addEventListener('pointerup', stopPan);
  viewport.addEventListener('pointercancel', stopPan);
  viewport.addEventListener('lostpointercapture', stopPan);
  viewport.addEventListener('click', event => {
    if (suppressClick) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  minus.onclick = () => setZoom(zoom - .25);
  plus.onclick = () => setZoom(zoom + .25);
  fit.onclick = () => setZoom(1);
  new MutationObserver(() => {
    sync();
    if (viewport.dataset.render === '3d') viewport.scrollTo(0, 0);
  }).observe(viewport, { attributes: true, attributeFilter: ['data-render'] });
  sync();
  return { reset: () => setZoom(1) };
}
