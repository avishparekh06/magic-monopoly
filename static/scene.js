// The storybook backdrop behind a page: a night sky with a moon and twinkling
// stars, drifting paper clouds, Rapunzel's tower with her braid swaying in the
// wind, grass along the bottom, and an optional wooden sign. Also turns the
// cursor into a fairy wand that leaves sparkles. Pure decoration
// (aria-hidden). Colors and motion live in fairytale.css.

// x = where it starts, in % of the screen width; y = top in %;
// w = width in px at full size; d = seconds to cross the whole sky
const CLOUDS = [
  { x: 14, y: 3, w: 300, d: 150, far: true },
  { x: 30, y: 7, w: 250, d: 95 },
  { x: 55, y: 15, w: 340, d: 170, far: true },
  { x: 72, y: 19, w: 230, d: 110 },
  { x: 88, y: 5, w: 170, d: 80 },
  { x: 2, y: 25, w: 280, d: 140, far: true },
];

export function mountScene({ greeting } = {}) {
  const scene = document.createElement("div");
  scene.className = "scene";
  scene.setAttribute("aria-hidden", "true");
  scene.innerHTML = defs() + stars() + moon() + CLOUDS.map(cloud).join("")
    + `<div class="tower-wrap">${tower()}</div>` + grass();
  if (greeting) scene.append(sign(greeting));
  document.body.prepend(scene);
  fairyDust();
}

function defs() {
  // Flat-bottomed paper cloud, reused by every cloud.
  return `<svg width="0" height="0" style="position:absolute"><defs>
    <path id="cloud-shape" d="M22 84a19 19 0 0 1 0-38a27 27 0 0 1 40-23a35 35 0 0 1 62 3a25 25 0 0 1 42 17a21 21 0 0 1 0 41z"/>
  </defs></svg>`;
}

// Same sky every load: a tiny seeded random number generator.
function seeded(seed) {
  return () => (seed = (seed * 16807) % 2147483647) / 2147483647;
}

function stars() {
  const rand = seeded(7);
  const dots = range(0, 90).map(() => {
    const big = rand() < 0.15;
    const style = `left:${(rand() * 100).toFixed(1)}%;top:${(rand() * 70).toFixed(1)}%;`
      + `--size:${big ? 12 : (2 + rand() * 2.5).toFixed(1)}px;--twinkle:${(2 + rand() * 3).toFixed(1)}s;animation-delay:${(-rand() * 5).toFixed(1)}s`;
    return `<span class="star${big ? " big" : ""}" style="${style}"></span>`;
  });
  return `<div class="stars">${dots.join("")}</div>`;
}

function moon() {
  return `<svg class="moon" viewBox="0 0 100 100"><path d="M50 10a40 40 0 1 0 0 80a50 50 0 0 1 0-80z"/></svg>`;
}

function cloud({ x, y, w, d, far }) {
  // A negative delay starts each cloud partway across, at x%.
  return `<div class="cloud${far ? " far" : ""}" style="top:${y}%;--w:${w};--drift:${d}s;animation-delay:${-(x / 100) * d}s">
    <svg viewBox="0 0 200 96"><use href="#cloud-shape" class="shadow" x="7" y="6"/><use href="#cloud-shape" class="face"/></svg>
  </div>`;
}

const range = (a, b) => Array.from({ length: b - a }, (_, i) => a + i);

function tower() {
  // Stones on the lit side of the body, in staggered rows.
  const stones = range(0, 13).map((r) => {
    const y = 330 + r * 44;
    return (r % 2 ? [110, 150] : [126, 166])
      .map((x, i) => `<rect x="${x}" y="${y}" width="${i ? 24 : 30}" height="13" rx="6"/>`).join("");
  }).join("");
  const corbels = range(0, 6).map((i) => `<path d="M${94 + i * 24} 306a11 11 0 0 0 22 0z"/>`).join("");
  const leaves = [
    [100, 860, -30, 1], [116, 812, 40, 0], [98, 770, -40, 1], [114, 724, 30, 1], [102, 680, -20, 0],
    [118, 632, 40, 1], [104, 590, -35, 1], [118, 548, 25, 0], [108, 506, -30, 1], [116, 466, 35, 1],
  ].map(([x, y, r, light]) =>
    `<ellipse class="${light ? "leaf" : "leaf dark"}" cx="${x}" cy="${y}" rx="13" ry="7" transform="rotate(${r} ${x} ${y})"/>`).join("");

  return `<svg class="tower" viewBox="0 -44 320 944" preserveAspectRatio="xMidYMax meet">
    <defs>
      <radialGradient id="window-glow">
        <stop offset="0" stop-color="#ffd27a" stop-opacity=".6"/>
        <stop offset="1" stop-color="#ffd27a" stop-opacity="0"/>
      </radialGradient>
    </defs>
    <line x1="160" y1="10" x2="160" y2="-40" class="pole"/>
    <path class="flag" d="M161 -40q20-6 38 2q-9 8 0 16q-18-6-38 2z"/>

    <path class="stone" d="M98 900L106 300H214L222 900z"/>
    <path class="stone-shade" d="M186 300H214L222 900H192z"/>
    <g class="stone-light">${stones}</g>
    <path class="ivy" d="M106 900C94 830 122 790 106 730S96 630 118 570S102 480 112 440"/>
    ${leaves}

    <rect class="stone-dark" x="86" y="282" width="148" height="24" rx="6"/>
    <g class="stone-dark">${corbels}</g>
    <path class="stone-light" d="M94 290L98 160H222L226 290z"/>
    <path class="stone-shade" d="M194 160H222L226 290H198z"/>
    <ellipse class="glow" cx="160" cy="232" rx="64" ry="56" fill="url(#window-glow)"/>
    <path class="window" d="M138 262V208a22 22 0 0 1 44 0V262z"/>
    <g class="rapunzel">
      <ellipse class="locks" cx="160" cy="226" rx="13" ry="15"/>
      <path class="dress" d="M143 262q17-30 34 0z"/>
      <circle class="face" cx="160" cy="226" r="8.5"/>
      <path class="locks" d="M151.5 225a8.5 8.5 0 0 1 17 0q-4-5-8.5-4q-4.5-1-8.5 4z"/>
      <circle class="eye" cx="157" cy="227" r="1.1"/><circle class="eye" cx="163" cy="227" r="1.1"/>
      <circle class="cheek" cx="155.5" cy="230" r="1.6"/><circle class="cheek" cx="164.5" cy="230" r="1.6"/>
    </g>
    <rect class="stone-dark" x="130" y="258" width="60" height="10" rx="4"/>

    <rect class="roof-shade" x="72" y="150" width="176" height="16" rx="8"/>
    <path class="roof" d="M78 156Q130 110 160 6Q190 110 242 156z"/>
    <path class="roof-shade" d="M160 6Q190 110 242 156H180Q174 80 160 6z"/>

    ${braid()}
  </svg>`;
}

// The braid: overlapping tilted ovals hanging from the window. The upper half
// swings from the sill and the lower half swings from the middle, a beat
// behind, so the hair bends like a rope in the wind.
function braid() {
  const x0 = 160, y0 = 262, step = 12, n = 36, split = 18;
  const yAt = (i) => y0 + 10 + i * step;
  const seg = (i) => {
    const side = i % 2 ? 1 : -1, x = x0 + side * 4, y = yAt(i);
    return `<ellipse cx="${x}" cy="${y}" rx="6.5" ry="13" transform="rotate(${side * -38} ${x} ${y})"/>`;
  };
  const end = yAt(n - 1) + 8;
  return `<g class="hair" style="transform-origin:${x0}px ${y0}px">
    ${range(0, split).map(seg).join("")}
    <g class="hair-lower" style="transform-origin:${x0}px ${yAt(split)}px">
      ${range(split, n).map(seg).join("")}
      <path d="M152 ${end}q-6 26-16 40q14-6 24 4q10-10 24-4q-10-14-16-40z"/>
      <path class="ribbon" d="M160 ${end}l-16-8v16zM160 ${end}l16-8v16z"/>
      <circle class="ribbon" cx="160" cy="${end}" r="5"/>
    </g>
  </g>
  <ellipse class="hair" cx="${x0}" cy="${y0}" rx="18" ry="8"/>`;
}

function grass() {
  // Three rows of scalloped bushes, back to front, each a repeating tile.
  const rows = [
    { y: 0, w: 150, cls: "back" },
    { y: 34, w: 130, cls: "mid" },
    { y: 70, w: 110, cls: "front" },
  ];
  const tile = (w) => {
    const bumps = range(0, 5).map((i) => {
      const x = (i + 1) * w / 5, h = 6 + ((i * 7) % 3) * 6;
      return `Q${x - w / 10} ${h} ${x} ${30 - (i % 2) * 6}`;
    }).join(" ");
    return `M0 30 ${bumps} V160 H0z`;
  };
  return `<svg class="grass" width="100%" height="150">
    <defs>${rows.map((r, i) =>
      `<pattern id="grass-${i}" patternUnits="userSpaceOnUse" x="${i * 37}" y="${r.y}" width="${r.w}" height="160"><path d="${tile(r.w)}" style="fill:var(--grass-${r.cls})"/></pattern>`).join("")}
    </defs>
    ${rows.map((r, i) => `<rect class="row-${r.cls}" x="-5%" y="${r.y}" width="110%" height="${150 - r.y}" fill="url(#grass-${i})"/>`).join("")}
  </svg>`;
}

function sign(text) {
  const el = document.createElement("div");
  el.className = "sign";
  el.innerHTML = `<svg class="rope" viewBox="0 0 280 46"><path d="M140 0L30 46M140 0L250 46"/></svg><div class="plank"></div>`;
  el.querySelector(".plank").textContent = text;
  return el;
}

// Sparkles trailing the wand cursor. Only with a mouse or trackpad (not on
// touch screens) and not when the viewer asked for reduced motion.
const SPARKLE_COLORS = ["#fff6c9", "#ffd76a", "#f7b6d2", "#cdb8ff"];
function fairyDust() {
  if (!matchMedia("(pointer: fine)").matches || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.documentElement.classList.add("wand");
  let last = 0, lastX = 0, lastY = 0;
  addEventListener("pointermove", (e) => {
    const now = performance.now();
    if (now - last < 28 || Math.hypot(e.clientX - lastX, e.clientY - lastY) < 6) return;
    last = now; lastX = e.clientX; lastY = e.clientY;
    const s = document.createElement("span");
    s.className = "sparkle";
    s.style.cssText = `left:${e.clientX}px;top:${e.clientY}px;--size:${(6 + Math.random() * 9).toFixed(1)}px;`
      + `--dx:${((Math.random() - 0.5) * 40).toFixed(1)}px;--dy:${(10 + Math.random() * 30).toFixed(1)}px;`
      + `--spin:${((Math.random() - 0.5) * 180).toFixed(0)}deg;`
      + `background:${SPARKLE_COLORS[Math.floor(Math.random() * SPARKLE_COLORS.length)]}`;
    s.addEventListener("animationend", () => s.remove());
    document.body.append(s);
  });
}
