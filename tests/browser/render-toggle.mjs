import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
// Run with the local server running: node tests/browser/render-toggle.mjs [origin]
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

 await send('Runtime.enable');await send('Emulation.setDeviceMetricsOverride',{width:1366,height:900,deviceScaleFactor:1,mobile:false});
 await send('Page.navigate',{url:origin+'/'});await waitFor('!!document.getElementById("create")');
 const setup=`(async()=>{
   for(const href of ['/static/style.css','/static/fairytale.css','/static/board-theme.css','/static/property-cards.css','/static/session-hub.css'])await new Promise((resolve,reject)=>{const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.onload=resolve;link.onerror=reject;document.head.append(link)});
   const {createBoardView,loadBoard}=await import('/static/board.js'),{mountBoardCamera}=await import('/static/board-camera.js'),{mountBoardActions}=await import('/static/board-actions.js');
   const data=await loadBoard(),cards=await(await fetch('/api/cards')).json();await document.fonts.ready;
   document.body.replaceChildren();document.body.className='board-page';document.body.style.display='block';
   for(const id of ['view-follow','view-full']){const button=document.createElement('button');button.id=id;button.textContent=id;document.body.append(button)}
   for(const id of ['board-view-location','board-view-status']){const span=document.createElement('span');span.id=id;document.body.append(span)}
   const viewport=document.createElement('div');viewport.id='board-viewport';viewport.className='board-viewport';viewport.style.cssText='width:100%;height:650px';
   const el=document.createElement('div');el.id='board';viewport.append(el);document.body.append(viewport);
   window.board=createBoardView(el,data);window.camera=mountBoardCamera(el,data,'toggle-fixture');window.sent=[];
   window.actions=mountBoardActions(viewport,data,cards,action=>sent.push(action));
   window.state={game_id:'toggle-fixture',phase:'WAITING_FOR_ROLL',turn:1,current_player:'p1',pending:null,owners:{},players:[{id:'p1',name:'Grandma',lane:1,position:0,balance:1500,kind:'in_person',properties:[]},{id:'p2',name:'Maya',lane:2,position:5,balance:1500,kind:'remote',properties:[]}]};
   window.update=()=>{board.render(state);camera.updateState(state);actions.update(state)};update();actions.connection('online');
   window.calls=0;if(window.WebGL2RenderingContext)for(const method of ['drawElements','drawElementsInstanced','drawArrays']){const original=WebGL2RenderingContext.prototype[method];WebGL2RenderingContext.prototype[method]=function(...args){calls++;return original.apply(this,args)}}
 })()`;
 // Legacy graphics preference must no longer disable Follow token.
 await evaluate(`localStorage.setItem('magic-monopoly:board-3d','off');localStorage.removeItem('magic-monopoly:board-view:toggle-fixture')`);
 await evaluate(setup);
 if(!await evaluate(`document.getElementById('board-viewport').dataset.render==='flat'&&!document.querySelector('.board-world')&&!performance.getEntriesByType('resource').some(r=>r.name.endsWith('/board-world.js'))&&getComputedStyle(document.getElementById('board')).visibility==='visible'&&!document.getElementById('game-3d-toggle')&&!document.getElementById('view-follow').disabled`))throw Error('Full map loaded WebGL or legacy preference blocked Follow');
 const snapshot=await evaluate('JSON.stringify(state)');
 await evaluate(`document.getElementById('view-follow').click();document.getElementById('view-full').click()`);await pause(350);
 if(!await evaluate(`document.getElementById('board-viewport').dataset.render==='flat'&&!document.querySelector('.board-world')`))throw Error('Returning to map during loading created a scene');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="3d" && calls>0');
 if(!await evaluate(`document.getElementById('board-viewport').dataset.view==='follow'&&getComputedStyle(document.getElementById('board')).visibility==='hidden'&&document.getElementById('view-follow').getAttribute('aria-pressed')==='true'`))throw Error('Follow did not open 3D');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="flat"');await pause(100);
 const stopped=await evaluate('calls');await pause(200);if(await evaluate('calls')!==stopped)throw Error('Full map continued rendering 3D');
 if(!await evaluate(`document.querySelector('.board-world').hidden&&!document.getElementById('game-map-toggle').hidden&&document.getElementById('game-reset-view').hidden&&document.querySelectorAll('.mm-sq').length===40&&getComputedStyle(document.querySelector('.mm-center')).backgroundImage.includes('board-garden.svg')`))throw Error('Full board or theme incorrect');
 if(await evaluate('JSON.stringify(state)')!==snapshot||await evaluate('sent.length')!==0)throw Error('Changing view changed gameplay');
 console.log('PASS: Full Board is themed 2D, Follow Token is 3D, no graphics toggle, safe loading race, and hidden 3D stops drawing');
 await send('Page.navigate',{url:origin+'/'});await waitFor('!!document.getElementById("create")');await evaluate(setup);
 if(!await evaluate(`document.getElementById('board-viewport').dataset.render==='flat'&&!document.querySelector('.board-world')`))throw Error('Full map preference lost on reload');
 await evaluate(`state={...state,phase:'MOVING',pending:{kind:'move',player:'p1',from:0,to:3},last_dice:null};update();document.getElementById('view-follow').click()`);
 await waitFor('document.getElementById("board-viewport").dataset.render==="3d"');await evaluate('document.getElementById("view-full").click()');
 await waitFor('!board.hasMotion()');
 if(!await evaluate(`document.querySelector('.mm-token[data-player="p1"]').dataset.square==='3'`))throw Error('Switching interrupted token movement');
 await evaluate(`state={...state,phase:'WAITING_FOR_DECISION',players:state.players.map(p=>p.id==='p1'?{...p,position:3}:p),pending:{kind:'decision',type:'buy',player:'p1',square:3,price:60}};update()`);
 await waitFor('!document.getElementById("turn-card").hidden');await evaluate('document.getElementById("turn-buy").click()');
 if(!await evaluate(`sent.length===1&&sent[0].action==='decision'&&sent[0].answer==='yes'`))throw Error('Flat board action changed');
 await send('Runtime.evaluate',{expression:'document.getElementById("game-fullscreen").click()',userGesture:true});await waitFor('!!document.fullscreenElement');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="3d"');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="flat"');
 if(!await evaluate('document.fullscreenElement.contains(document.getElementById("game-map-toggle"))'))throw Error('View switch missing inside fullscreen');
 await fs.writeFile('/private/tmp/magic-monopoly-flat-board.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await evaluate('document.getElementById("game-fullscreen").click()');await waitFor('!document.fullscreenElement');
 console.log('PASS: Map preference survives refresh, switching preserves movement, 2D decisions work, and fullscreen includes both views');
 for(const [width,height] of [[390,844],[320,720]]){
   await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:true});await pause(100);
   const fits=()=>evaluate(`(()=>{const v=document.getElementById('board-viewport').getBoundingClientRect(),tools=document.querySelector('.game-surface-tools').getBoundingClientRect();return tools.left>=v.left-1&&tools.right<=v.right+1})()`);
   if(!await fits())throw Error('Map toolbar overflows at '+width);
   await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="3d"');
   if(!await fits())throw Error('Follow toolbar overflows at '+width);
   await evaluate('document.getElementById("game-map-toggle").click()');
 }
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="3d"');
 await evaluate(`document.querySelector('.board-world').dispatchEvent(new Event('webglcontextlost',{cancelable:true}))`);
 await waitFor('document.getElementById("view-follow").disabled');
 if(!await evaluate(`document.getElementById('board-viewport').dataset.render==='flat'&&getComputedStyle(document.getElementById('board')).visibility==='visible'`))throw Error('Graphics failure did not leave a usable map');
 console.log('PASS: Mobile toolbars fit, and graphics failure leaves the full 2D board playable');
}catch(e){
 console.error(e);
 if(ws?.readyState===1){
  ws.send(JSON.stringify({id:9999,method:'Runtime.evaluate',params:{expression:'document.body.innerText',returnByValue:true}}));
  ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id===9999)console.error('Rendered page:',m.result?.result?.value?.slice(0,1500));};
  await pause(500);
 }
 process.exitCode=1;
}finally{ws?.close();chrome.kill();}
