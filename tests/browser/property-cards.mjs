import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
// Run with the local server running: node tests/browser/property-cards.mjs [origin]
// Uses isolated presentation fixtures; never rolls or modifies a saved game.
const origin = process.argv[2] || 'http://localhost:8000';
const profile = await fs.mkdtemp('/tmp/classscheduler-ui-');
const chrome = spawn(process.env.CHROME_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', ['--headless','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist','--no-first-run','--no-default-browser-check',`--user-data-dir=${profile}`,'--remote-debugging-port=0','about:blank'], {stdio:'ignore'});
let ws;
const pause = ms => new Promise(r=>setTimeout(r,ms));
try {
 let port;
 for(let i=0;i<100;i++){try{port=(await fs.readFile(`${profile}/DevToolsActivePort`,'utf8')).split('\n')[0];break;}catch{await pause(100);}}
 if(!port) throw Error('Chrome did not start');
 let pages=await(await fetch(`http://localhost:${port}/json/list`)).json();
 ws = new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
 await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j;});
 let seq=0;const waiting=new Map();
 ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id){const p=waiting.get(m.id);waiting.delete(m.id);m.error?p.reject(m.error):p.resolve(m.result);}};
 const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;waiting.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
 const evaluate=async expression=>{const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 const waitFor=async expression=>{for(let i=0;i<150;i++){if(await evaluate(expression))return;await pause(200);}throw Error(`Timed out: ${expression}`);};

 await send('Runtime.enable');await send('Page.navigate',{url:origin+'/'});await waitFor('!!document.getElementById("create")');
 await send('Emulation.setDeviceMetricsOverride',{width:1440,height:950,deviceScaleFactor:1,mobile:false});
 await evaluate(`(async()=>{
   for(const href of ['/static/style.css','/static/board-theme.css','/static/property-cards.css'])await new Promise((resolve,reject)=>{const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.onload=resolve;link.onerror=reject;document.head.append(link)});
   await document.fonts.ready;
   const {mountBoardPlayers}=await import('/static/board-players.js');
   const {renderPropertyCards}=await import('/static/property-cards.js');
   window.boardData=await(await fetch('/api/board')).json();
   document.body.replaceChildren();document.body.className='board-page';
   const viewport=document.createElement('div');viewport.id='fixture-viewport';viewport.className='board-viewport';viewport.style.cssText='width:100%;height:800px;background:#203c51';document.body.append(viewport);
   window.sendCount=0;window.fixture=mountBoardPlayers(viewport,boardData,()=>window.sendCount++);
   const p=(id,name,lane,properties,balance=1500)=>({id,name,lane,properties,balance,position:0,bankrupt:false,jail_free_cards:0});
   window.s={game_id:'fixture',phase:'WAITING_FOR_ROLL',turn:1,current_player:'p1',pending:null,players:[p('p1','Grandma',1,[19,29],1220),{...p('p2','Alexandria the Wandering Princess',2,[6,8,9,5,15,12,28]),jail_free_cards:2},p('p3','Guest',3,[])]};
   window.originalState=JSON.stringify(s);fixture.update(s);fixture.open('p1');
 })()`);
 await waitFor('document.getElementById("game-properties")?.open && document.querySelectorAll(".property-deed").length===2');
 const contrast=await evaluate(`(()=>{
   const rgb=css=>css.match(/[0-9.]+/g).slice(0,3).map(Number),lum=channels=>channels.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   const fg=lum(rgb(getComputedStyle(document.querySelector('#game-properties header button')).color)),bg=lum(rgb(getComputedStyle(document.getElementById('game-properties')).backgroundColor));return (Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05);
 })()`);
 if(contrast<4.5)throw Error('Card browser buttons have insufficient contrast '+contrast);
 await evaluate(`Promise.all([...document.querySelectorAll('.property-deed img')].map(img=>img.decode()))`);
 await fs.writeFile('/private/tmp/magic-monopoly-property-cards.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 const facts=await evaluate(`[...document.querySelectorAll('.property-deed')].map(card=>({id:card.dataset.square,text:card.querySelector('.property-deed-values').innerText,art:card.querySelector('img').naturalWidth}))`);
 if(facts[0].id!=='19'||!facts[0].text.includes('16 gold')||facts[1].id!=='29'||!facts[1].text.includes('24 gold')||facts.some(c=>!c.art))throw Error('Original screenshot collection incorrect '+JSON.stringify(facts));
 const moneyCheck=await evaluate(`(async()=>{
   const {banknoteBreakdown}=await import('/static/player-money.js');
   const balances=[0,1,4,5,6,10,19,20,49,50,99,100,378,1220,1500,99999];
   const exact=balances.every(balance=>{const notes=banknoteBreakdown(balance);return notes.length<=7&&notes.every(n=>Number.isInteger(n.count)&&n.count>0)&&notes.reduce((sum,n)=>sum+n.value*n.count,0)===balance});
   const small=[...document.querySelectorAll('.property-deed')].every(card=>card.getBoundingClientRect().width<=150&&card.getBoundingClientRect().height<300);
   const defaultOpen=document.querySelector('.player-money').open;
   document.querySelector('.player-money summary').click();
   fixture.update(s);
   const staysHidden=!document.querySelector('.player-money').open;
   document.querySelector('.player-money summary').click();
   const total=()=>[...document.querySelectorAll('.money-note-stack')].reduce((sum,n)=>sum+Number(n.dataset.value)*Number(n.dataset.count),0);
   const shown=total()===1220;
   s.players[0].balance=378;fixture.update(s);
   const live=document.querySelector('.player-money').open&&total()===378&&document.querySelector('.money-wallet-total').textContent.includes('378');
   s.players[0].balance=1220;const owned=s.players[0].properties;s.players[0].properties=[6];fixture.update(s);
   const single=document.querySelectorAll('.property-deed').length===1&&document.querySelector('.property-deed').getBoundingClientRect().width<=150;
   document.querySelector('.property-deed summary').click();
   const expandedSmall=document.querySelector('.property-deed-art').getBoundingClientRect().width<=150;
   s.players[0].properties=owned;fixture.update(s);
   return {exact,small,defaultOpen,staysHidden,shown,live,single,expandedSmall};
 })()`);
 if(Object.values(moneyCheck).some(value=>!value))throw Error('Compact cards or money failed '+JSON.stringify(moneyCheck));
 await fs.writeFile('/private/tmp/magic-monopoly-show-money.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 console.log('PASS: Single and expanded cards stay small; Money starts visible, hiding survives updates, totals match real balances, and open wallets update live');
 await evaluate('document.querySelector(".game-property-tabs [data-player=p2]").click()');
 if(!await evaluate(`[...document.querySelectorAll('.money-note-stack')].reduce((sum,n)=>sum+Number(n.dataset.value)*Number(n.dataset.count),0)===1500`))throw Error('Switching player kept another balance');
 if(await evaluate('document.querySelectorAll(".property-deed").length')!==7)throw Error('Player switch lost cards');
 const rents=await evaluate(`Object.fromEntries([...document.querySelectorAll('.property-deed')].map(card=>[card.dataset.square,card.querySelector('.property-deed-values').innerText]))`);
 if(!rents['6'].includes('12 gold')||!rents['9'].includes('16 gold')||!rents['5'].includes('50 gold')||!rents['12'].includes('10 per dice point'))throw Error('Rent semantics changed '+JSON.stringify(rents));
 if(!await evaluate(`document.querySelector('.property-deed[data-square="6"]').innerText.includes('Complete tale')&&document.querySelector('.property-escape').innerText.includes('2 escape cards held')`))throw Error('Set completion or escape hand missing');
 await evaluate(`document.querySelector('.property-deed[data-square="5"] summary').click()`);await pause(50);
 if(!await evaluate(`document.querySelector('.property-deed[data-square="5"]').open&&document.querySelector('.property-deed[data-square="5"] .property-rent-schedule').innerText.includes('200 gold')`))throw Error('Workshop details missing');
 await evaluate(`document.querySelector('.property-deed[data-square="6"] summary').click()`);await pause(50);
 if(await evaluate('document.querySelectorAll(".property-deed[open]").length')!==1)throw Error('More than one expanded card');
 await evaluate('fixture.update(s)');await pause(50);
 if(!await evaluate(`document.querySelector('.property-deed[data-square="6"]').open`))throw Error('Refresh closed selected card');
 await fs.writeFile('/private/tmp/magic-monopoly-property-card-expanded.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 console.log('PASS: Illustrated property collection, correct base/full-set/workshop/utility rents, escape cards and one expanded deed across updates');
 await evaluate('document.querySelector(".game-property-tabs [data-player=p3]").click()');
 if(!await evaluate(`document.querySelector('.property-empty').innerText.includes('No property cards yet')&&!document.querySelector('.property-deed')`))throw Error('Empty collection incorrect');
 await evaluate('s.players[2].balance=0;fixture.update(s)');
 if(!await evaluate(`document.querySelector('.money-wallet-total').textContent.includes('0')&&!document.querySelector('.money-note-stack')&&document.querySelector('.money-empty').textContent==='No gold remaining.'`))throw Error('Zero balance invented banknotes');
 await evaluate('s.players[2].balance=1500;fixture.update(s);fixture.open("p1")');
 for(const [width,height] of [[390,844],[320,720]]){
   await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:true});await pause(100);
   await evaluate(`document.querySelector('.property-deed summary').click()`);await pause(50);
   const fit=await evaluate(`(()=>{const d=document.getElementById('game-properties');return {width:d.clientWidth,scroll:d.scrollWidth,screen:innerWidth,document:document.documentElement.scrollWidth,art:document.querySelector('.property-deed-art').getBoundingClientRect().width}})()`);
   if(fit.scroll>fit.width+1||fit.document>fit.screen+1||fit.art>150)throw Error('Mobile card overflow '+JSON.stringify(fit));
   if(width===390)await fs.writeFile('/private/tmp/magic-monopoly-property-card-phone.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 }
 await send('Emulation.setDeviceMetricsOverride',{width:1366,height:768,deviceScaleFactor:1,mobile:false});
 await evaluate('document.getElementById("game-properties").close()');
 await send('Runtime.evaluate',{expression:'document.getElementById("fixture-viewport").requestFullscreen()',userGesture:true});await waitFor('!!document.fullscreenElement');
 await evaluate('fixture.open("p2")');
 if(!await evaluate('document.fullscreenElement.contains(document.getElementById("game-properties"))'))throw Error('Card collection escaped fullscreen');
 await evaluate(`document.querySelector('.property-deed[data-square="28"] summary').click()`);await pause(50);
 if(!await evaluate(`document.querySelector('.property-deed[data-square="28"]').innerText.includes('10 gold per dice point')`))throw Error('Wish details missing');
 if(!await evaluate('document.querySelector(".player-money").open && document.fullscreenElement.contains(document.querySelector(".money-wallet"))'))throw Error('Money missing inside fullscreen');
 await evaluate('document.getElementById("game-properties").close();document.exitFullscreen()');
 if(!await evaluate('JSON.stringify(s)===originalState && sendCount===0'))throw Error('Browsing changed gameplay');
 await evaluate(`s.players[0].properties=boardData.squares.filter(sq=>['property','railroad','utility'].includes(sq.type)).map(sq=>sq.id);fixture.update(s);fixture.open('p1')`);
 await evaluate(`Promise.all([...document.querySelectorAll('.property-deed img')].map(img=>{img.loading='eager';return img.decode()}))`);
 if(!await evaluate(`document.querySelectorAll('.property-deed').length===28&&[...document.querySelectorAll('.property-deed img')].every(img=>img.naturalWidth>0)`))throw Error('Incomplete artwork coverage');
 console.log('PASS: All 28 pictures load, mobile and fullscreen fit, and browsing changes no game state');
 await evaluate(`(async()=>{
   document.getElementById('game-properties').close();document.body.className='';document.body.replaceChildren();
   const panel=document.createElement('section');panel.className='panel property-collection';panel.style.cssText='max-width:520px;margin:16px auto';document.body.append(panel);
   const {renderPropertyCards}=await import('/static/property-cards.js');panel.append(renderPropertyCards(boardData,s.players[1]));
 })()`);
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await pause(100);
 if(!await evaluate('document.documentElement.scrollWidth<=innerWidth+1'))throw Error('Phone My cards overflow');
 await fs.writeFile('/private/tmp/magic-monopoly-phone-property-collection.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 console.log('PASS: Shared My cards collection fits the phone panel; card browser controls have readable contrast');
}catch(e){
 console.error(e);
 if(ws?.readyState===1){
  ws.send(JSON.stringify({id:9999,method:'Runtime.evaluate',params:{expression:'document.body.innerText',returnByValue:true}}));
  ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id===9999)console.error('Rendered page:',m.result?.result?.value?.slice(0,1500));};
  await pause(500);
 }
 process.exitCode=1;
}finally{ws?.close();chrome.kill();}
