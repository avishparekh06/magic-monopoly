// Cute default avatars, one per seat: Grandma, a frog prince and a baby
// dragon. Each is an SVG drawn on top of the player's color (the crest
// background), so it works at any size.

const INK = "#2f2433", SKIN = "#f3cba8", CHEEK = "#ec8f95";

const AVATARS = {
  // Grandma: white bun, round glasses, rosy cheeks, lilac cardigan
  p1: `
    <path d="M7 41q13-14 26 0z" fill="#cfa6d8"/>
    <circle cx="20" cy="7.5" r="5" fill="#ece7f2"/>
    <ellipse cx="20" cy="18" rx="12.5" ry="10.5" fill="#ece7f2"/>
    <circle cx="20" cy="23" r="9.5" fill="${SKIN}"/>
    <path d="M10.5 21q9.5-11 19 0q-4-5.5-9.5-5.5t-9.5 5.5z" fill="#ece7f2"/>
    <circle cx="16" cy="23.5" r="3" fill="none" stroke="${INK}" stroke-width="1.1"/>
    <circle cx="24" cy="23.5" r="3" fill="none" stroke="${INK}" stroke-width="1.1"/>
    <path d="M19 23.5h2" stroke="${INK}" stroke-width="1.1"/>
    <circle cx="16" cy="23.8" r=".95" fill="${INK}"/><circle cx="24" cy="23.8" r=".95" fill="${INK}"/>
    <circle cx="13.2" cy="27.6" r="1.8" fill="${CHEEK}" opacity=".75"/><circle cx="26.8" cy="27.6" r="1.8" fill="${CHEEK}" opacity=".75"/>
    <path d="M17.6 28.6q2.4 2 4.8 0" fill="none" stroke="${INK}" stroke-width="1.1" stroke-linecap="round"/>`,

  // Frog prince: big round eyes and a little gold crown
  p2: `
    <ellipse cx="20" cy="27" rx="13.5" ry="10.5" fill="#8cc56b"/>
    <circle cx="13" cy="18" r="5.2" fill="#8cc56b"/><circle cx="27" cy="18" r="5.2" fill="#8cc56b"/>
    <circle cx="13" cy="18" r="3.3" fill="#fff"/><circle cx="27" cy="18" r="3.3" fill="#fff"/>
    <circle cx="13.6" cy="18.6" r="1.7" fill="${INK}"/><circle cx="27.6" cy="18.6" r="1.7" fill="${INK}"/>
    <circle cx="14.1" cy="17.9" r=".6" fill="#fff"/><circle cx="28.1" cy="17.9" r=".6" fill="#fff"/>
    <path d="M15 16.5l1-6 2.6 3.2L20 9l1.4 4.7L24 10.5l1 6z" fill="#f2c14e" stroke="#b07d22" stroke-width=".8" stroke-linejoin="round"/>
    <circle cx="11" cy="27.5" r="2" fill="${CHEEK}" opacity=".7"/><circle cx="29" cy="27.5" r="2" fill="${CHEEK}" opacity=".7"/>
    <path d="M14.5 28.5q5.5 4.5 11 0" fill="none" stroke="#3d6b3a" stroke-width="1.3" stroke-linecap="round"/>`,

  // Baby dragon: tiny horns, gold spikes, sleepy-happy face
  p3: `
    <path d="M12.5 13l-2.5-7 6 4.5zM27.5 13l2.5-7-6 4.5z" fill="#f6e7c8"/>
    <path d="M16.5 12.5l3.5-5 3.5 5z" fill="#f2c14e"/>
    <ellipse cx="20" cy="23" rx="12.5" ry="11.5" fill="#9b7fd1"/>
    <ellipse cx="20" cy="28" rx="7.5" ry="5.2" fill="#c3b0ea"/>
    <circle cx="17.8" cy="27.2" r=".85" fill="${INK}"/><circle cx="22.2" cy="27.2" r=".85" fill="${INK}"/>
    <circle cx="14.8" cy="20.5" r="2.3" fill="${INK}"/><circle cx="25.2" cy="20.5" r="2.3" fill="${INK}"/>
    <circle cx="15.6" cy="19.7" r=".85" fill="#fff"/><circle cx="26" cy="19.7" r=".85" fill="#fff"/>
    <circle cx="11.3" cy="25" r="1.9" fill="${CHEEK}" opacity=".75"/><circle cx="28.7" cy="25" r="1.9" fill="${CHEEK}" opacity=".75"/>
    <path d="M18 30.4q2 1.6 4 0" fill="none" stroke="${INK}" stroke-width="1.1" stroke-linecap="round"/>`,
};

// SVG markup for a player's avatar, or null for a seat without one.
export function avatarSvg(pid) {
  const art = AVATARS[pid];
  return art ? `<svg viewBox="0 0 40 40" aria-hidden="true">${art}</svg>` : null;
}
