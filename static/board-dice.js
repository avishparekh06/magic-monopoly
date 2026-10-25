// Presentation only: server-provided dice faces and a toss in the existing board view.
import * as THREE from '/static/vendor/three/three.module.min.js';

import { DICE_SHAKE_MS, DICE_TOSS_MS, DICE_HOLD_MS } from '/static/board-motion.js';
export { DICE_TOSS_MS } from '/static/board-motion.js';

export const DICE_SIZE = .9;
const TILE_WIDTH = 44 / 12.2;
const EDGE_CLEARANCE = DICE_SIZE * Math.sqrt(3) / 2 + .08;
function constrainToSquare(position, origin) {
  const w = origin.w || TILE_WIDTH, d = origin.d || TILE_WIDTH;
  position.x = THREE.MathUtils.clamp(position.x, origin.x - w / 2 + EDGE_CLEARANCE, origin.x + w / 2 - EDGE_CLEARANCE);
  position.z = THREE.MathUtils.clamp(position.z, origin.z - d / 2 + EDGE_CLEARANCE, origin.z + d / 2 - EDGE_CLEARANCE);
  return position;
}
// A short tumble within one square, shared by placement checks and animation.
export function dicePath(origin, index, t) {
  const d = origin.d || TILE_WIDTH;
  const offset = (index ? .82 : -.82) + .06 * Math.sin(Math.PI * 2 * t + index * Math.PI) * (1 - t);
  const spot = origin.spots?.[index];
  const wiggle = .04 * Math.sin(Math.PI * 2 * t + index * Math.PI) * (1 - t);
  const x = origin.x + (spot ? spot.x + wiggle : (origin.offsetX || 0) + (origin.axis === 'z' ? 0 : offset));
  const z = origin.z + (spot ? spot.z - wiggle : (origin.offsetZ ?? -d * .25) + (origin.axis === 'z' ? offset : 0));
  return constrainToSquare(new THREE.Vector3(x, 0, z), origin);
}
export function diceFaceOrientation(face) {
  const rotation = {
    1: [0, 0, 0], 6: [0, 0, Math.PI],
    2: [-Math.PI / 2, 0, 0], 5: [Math.PI / 2, 0, 0],
    3: [0, 0, Math.PI / 2], 4: [0, 0, -Math.PI / 2],
  }[face];
  return new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotation));
}
export function createDiceThrow(scene, reduced) {
  const size = DICE_SIZE, half = size / 2, bevel = .08;
  const geometry = new THREE.BoxGeometry(size, size, size, 8, 8, 8);
  const position = geometry.attributes.position, normals = geometry.attributes.normal;
  const point = new THREE.Vector3(), inner = new THREE.Vector3(), normal = new THREE.Vector3();
  for (let i = 0; i < position.count; i++) {
    point.fromBufferAttribute(position, i);
    inner.set(...point.toArray().map(v => THREE.MathUtils.clamp(v, -half + bevel, half - bevel)));
    normal.copy(point).sub(inner).normalize();
    point.copy(inner).addScaledVector(normal, bevel);
    position.setXYZ(i, point.x, point.y, point.z); normals.setXYZ(i, normal.x, normal.y, normal.z);
  }
  const pips = {
    1: [[0, 0]], 2: [[-1, -1], [1, 1]], 3: [[-1, -1], [0, 0], [1, 1]],
    4: [[-1, -1], [1, -1], [-1, 1], [1, 1]],
    5: [[-1, -1], [1, -1], [0, 0], [-1, 1], [1, 1]],
    6: [[-1, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [1, 1]],
  };
  const materials = Array.from({ length: 6 }, (_, i) => {
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff1d3'; ctx.fillRect(0, 0, 256, 256);
    ctx.strokeStyle = '#c4a26b'; ctx.lineWidth = 4; ctx.strokeRect(9, 9, 238, 238);
    for (const [x, y] of pips[i + 1]) {
      ctx.beginPath(); ctx.arc(128 + x * 61, 128 + y * 61, 20, 0, Math.PI * 2); ctx.fillStyle = '#49354e'; ctx.fill();
      ctx.beginPath(); ctx.arc(123 + x * 61, 123 + y * 61, 6, 0, Math.PI * 2); ctx.fillStyle = '#6d4f66'; ctx.fill();
    }
    const texture = new THREE.CanvasTexture(canvas); texture.colorSpace = THREE.SRGBColorSpace;
    return new THREE.MeshStandardMaterial({ map: texture, roughness: .4, metalness: .025 });
  });
  // BoxGeometry material order: +X, -X, +Y, -Y, +Z, -Z. Opposite faces add to 7.
  const ordered = [3, 4, 1, 6, 2, 5].map(n => materials[n - 1]);
  const dice = [0, 1].map(() => {
    const mesh = new THREE.Mesh(geometry, ordered); mesh.castShadow = mesh.receiveShadow = true;
    mesh.visible = false; scene.add(mesh); return mesh;
  });
  const orientation = new THREE.Matrix4();
  let roll = null;
  function start(faces, origin, now = performance.now()) {
    if (!Array.isArray(faces) || faces.length !== 2 || faces.some(n => !Number.isInteger(n) || n < 1 || n > 6)) return;
    const targets = [0, 1].map(i => dicePath(origin, i, 1).setY(.32 + half));
    const rotations = faces.map((face, i) => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), i ? .3 : -.2).multiply(diceFaceOrientation(face)));
    roll = { faces, targets, rotations, origin, started: now };
    // Reset positions before exposing the dice; never show the previous throw.
    update(now);
  }
  function update(now) {
    if (!roll) return null;
    const duration = reduced ? 0 : DICE_TOSS_MS;
    const elapsed = now - roll.started;
    const shaking = !reduced && elapsed < DICE_SHAKE_MS;
    const t = duration ? THREE.MathUtils.clamp((elapsed - DICE_SHAKE_MS) / (duration - DICE_SHAKE_MS), 0, 1) : 1;
    const bounce = shaking ? .04 : .55 * (1 - t) ** 1.2 * Math.abs(Math.sin(3 * Math.PI * t));
    dice.forEach((mesh, i) => {
      mesh.visible = elapsed < duration + DICE_HOLD_MS;
      mesh.position.copy(dicePath(roll.origin, i, t));
      if (shaking) {
        mesh.position.x += Math.sin(elapsed * .065 + i) * .035;
        mesh.position.z += Math.cos(elapsed * .055 + i) * .035;
      }
      constrainToSquare(mesh.position, roll.origin);
      const spin = new THREE.Quaternion().setFromEuler(new THREE.Euler((1 - t) * Math.PI * (i ? 3.5 : -4.5), (1 - t) * Math.PI * 3.25, (1 - t) * Math.PI * 2.75));
      mesh.quaternion.copy(spin).multiply(roll.rotations[i]);
      if (shaking) mesh.quaternion.premultiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(
        Math.sin(elapsed * .06) * .22, Math.cos(elapsed * .06) * .18, 0)));
      // Support the rotated rounded cube above the board rather than clipping
      // its corners through the surface on the last few bounces.
      const m = orientation.makeRotationFromQuaternion(mesh.quaternion).elements;
      const support = (half - bevel) * (Math.abs(m[1]) + Math.abs(m[5]) + Math.abs(m[9])) + bevel;
      mesh.position.y = roll.targets[i].y - half + support + bounce;
    });
    const active = !reduced && now - roll.started < duration + DICE_HOLD_MS;
    return { active, settled: t === 1 };
  }
  const bounds = (camera, min, max) => {
    const points = [];
    for (const x of [min.x, max.x]) for (const y of [min.y, max.y]) for (const z of [min.z, max.z])
      points.push(new THREE.Vector3(x, y, z).project(camera));
    return { left: Math.min(...points.map(p => p.x)), right: Math.max(...points.map(p => p.x)),
      bottom: Math.min(...points.map(p => p.y)), top: Math.max(...points.map(p => p.y)) };
  };
  const tokenBounds = (camera, tokens) => tokens.map(p => ({
    rect: bounds(camera, new THREE.Vector3(p.x - .6, p.y, p.z - .6), new THREE.Vector3(p.x + .6, p.y + 1.95, p.z + .6)),
    badge: bounds(camera, new THREE.Vector3(p.x - .37, p.y + 1.85, p.z - .37), new THREE.Vector3(p.x + .37, p.y + 2.55, p.z + .37)),
    position: p, depth: p.clone().add(new THREE.Vector3(0, 1.1, 0)).applyMatrix4(camera.matrixWorldInverse).z
  }));
  const obscures = (camera, center, rect, blocker) => (overlaps(rect, blocker.rect) || overlaps(rect, blocker.badge)) && (
    Math.hypot(center.x - blocker.position.x, center.z - blocker.position.z) < 1.1 ||
    center.clone().applyMatrix4(camera.matrixWorldInverse).z > blocker.depth
  );
  const overlaps = (a, b) => a.left <= b.right && a.right >= b.left && a.bottom <= b.top && a.top >= b.bottom;
  function landingClearance(camera, origin, tokens) {
    const blockers = tokenBounds(camera, tokens);
    let margin = Infinity;
    for (let i = 0; i < 2; i++) {
      const center = dicePath(origin, i, 1).setY(.32 + half);
      const depth = center.clone().project(camera).z;
      if (depth < -1 || depth > 1) return -10;
      // At rest only the small yaw remains; this box also allows for its rotation.
      const rect = bounds(camera, center.clone().add(new THREE.Vector3(-size * .66, -half, -size * .66)), center.clone().add(new THREE.Vector3(size * .66, half, size * .66)));
      if (blockers.some(b => obscures(camera, center, rect, b))) return -1;
      margin = Math.min(margin, rect.left + 1, 1 - rect.right, rect.bottom + 1, 1 - rect.top);
    }
    return margin;
  }
  // A last screen-space check also handles an orbited camera: a die must never
  // hide a piece or its player badge, even when their world positions are clear.
  function avoidTokens(camera, tokens) {
    if (!dice.some(mesh => mesh.visible) || !tokens.length) return;
    camera.updateMatrixWorld();
    const blockers = tokenBounds(camera, tokens);
    for (const mesh of dice) {
      if (!mesh.visible) continue;
      mesh.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(mesh), rect = bounds(camera, box.min, box.max);
      if (blockers.some(b => obscures(camera, mesh.position, rect, b))) mesh.visible = false;
    }
  }
  return { start, update, avoidTokens, landingClearance, clear() { roll = null; dice.forEach(mesh => { mesh.visible = false; }); } };
}
