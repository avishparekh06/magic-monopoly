// Small monochrome board illustrations, intentionally separate from card/game data.
const paths = {
  book: '<path d="M12 5c-3-2-6-2-9-1v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1Z"/><path d="M12 5v15M6 8h3M6 11h3M15 8h3M15 11h3"/>',
  scroll: '<path d="M6 5h12v13a3 3 0 0 1-3 3H6a3 3 0 0 1 0-6h12M6 5V3a2 2 0 0 1 4 0v2M9 9h6M9 12h5"/>',
  orb: '<circle cx="12" cy="9" r="6"/><path d="m7 19 2-4h6l2 4H7ZM9 7l1-1"/>',
  tower: '<path d="M7 21V8h10v13M5 8l7-6 7 6H5ZM10 21v-5h4v5M11 10h2v3h-2Z"/>',
  house: '<path d="m3 11 9-8 9 8M5 9v12h14V9M10 21v-7h4v7M7 11h2M15 11h2"/>',
  lamp: '<path d="M5 13h12c0 4-2 6-6 6s-6-2-6-6ZM17 14l4-4v4l-4 2M5 13H3V9h4M9 9h4M10 5c-2-2 3-2 1-4M8 21h7"/>',
  moon: '<path d="M19 16A9 9 0 0 1 8 3a9 9 0 1 0 11 13Z"/>',
  well: '<path d="m3 8 9-6 9 6H3ZM6 8v8M18 8v8M5 16h14v5H5ZM12 8v8M10 12h4v4h-4"/>',
  gate: '<path d="M4 21V7h4v14M16 21V7h4v14M8 10h8M8 15h8M11 10v11M14 10v11M3 7l3-5 3 5M15 7l3-5 3 5"/>',
  balance: '<path d="M12 3v18M7 21h10M4 7h16M6 7l-4 7h8L6 7ZM18 7l-4 7h8l-4-7Z"/>',
};
const bySquare = {0:'book',2:'scroll',4:'balance',5:'house',7:'orb',10:'tower',12:'lamp',15:'house',17:'scroll',20:'moon',22:'orb',25:'house',28:'well',30:'gate',33:'scroll',35:'house',36:'orb',38:'balance'};
export function lineIcon(name, className='') {
  const span=document.createElement('span');span.className=className;span.setAttribute('aria-hidden','true');
  span.innerHTML=`<svg width="1.2em" height="1.2em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${paths[name]||paths.book}</svg>`;
  return span;
}
export function boardIcon(index) { return bySquare[index] ? lineIcon(bySquare[index],'mm-icon') : null; }
