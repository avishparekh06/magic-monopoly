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
   for(const href of ['/static/style.css','/static/fairytale.css','/static/board-theme.css','/static/board-map.css?v=classic-1','/static/property-cards.css','/static/session-hub.css'])await new Promise((resolve,reject)=>{const link=document.createElement('link');link.rel='stylesheet';link.href=href;link.onload=resolve;link.onerror=reject;document.head.append(link)});
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
 if(!await evaluate(`(()=>{const tools=document.querySelector('.game-surface-tools'),v=document.getElementById('board-viewport');return !v.contains(tools)&&tools.getBoundingClientRect().bottom<=v.getBoundingClientRect().top})()`))throw Error('Board controls overlap artwork');
 await evaluate('state={...state,last_roll:11};update()');
 if(!await evaluate(`(()=>{const badge=document.getElementById('last-roll-badge'),map=document.getElementById('board');return badge.textContent==='Last roll: 11'&&!badge.hidden&&!map.contains(badge)&&getComputedStyle(badge).backgroundColor==='rgb(255, 242, 213)'})()`))throw Error('Last roll is missing its readable toolbar badge');
 const snapshot=await evaluate('JSON.stringify(state)');
 const initialMapWidth=await evaluate('document.getElementById("board").getBoundingClientRect().width');
 if(!await evaluate(`(()=>{const b=document.getElementById('game-fullscreen'),r=b.getBoundingClientRect(),m=document.getElementById('board').getBoundingClientRect();return !!b.querySelector('svg')&&b.textContent.trim()===''&&!b.closest('.game-surface-tools')&&b.getAttribute('aria-label')==='Enter fullscreen'&&Math.abs(m.right-r.right)<=10&&Math.abs(m.bottom-r.bottom)<=10})()`))throw Error('Fullscreen icon is not in the 2D board corner');

 await evaluate(`document.querySelector('[aria-label="Zoom in on 2D board"]').click()`);await pause(150);
 if(!await evaluate(`document.getElementById('board').getBoundingClientRect().width>${initialMapWidth}*1.2&&(document.getElementById('board-viewport').scrollWidth>document.getElementById('board-viewport').clientWidth||document.getElementById('board-viewport').scrollHeight>document.getElementById('board-viewport').clientHeight)`))throw Error('2D zoom did not enlarge scrollable map');
 const pinnedCorner=await evaluate(`(()=>{const r=document.getElementById('game-fullscreen').getBoundingClientRect(),v=document.getElementById('board-viewport').getBoundingClientRect();if(Math.abs(r.right-v.right+8)>2||Math.abs(r.bottom-v.bottom+8)>2)throw Error('Zoomed fullscreen icon is not at the visible corner');return {x:r.x,y:r.y}})()`);
 await evaluate(`document.getElementById('board-viewport').scrollTo(0,0)`);
 const dragPoint=await evaluate(`(()=>{const r=document.getElementById('board-viewport').getBoundingClientRect();return {x:r.left+200,y:r.top+220}})()`);
 await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...dragPoint});
 await send('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:dragPoint.x-80,y:dragPoint.y-80});
 await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,x:dragPoint.x-80,y:dragPoint.y-80});
 if(!await evaluate(`document.getElementById('board-viewport').scrollTop>=70&&document.getElementById('board-viewport').scrollLeft>=70&&!document.querySelector('.map-panning')&&sent.length===0`))throw Error('Dragging did not pan the map safely');
 await pause(60);
 if(!await evaluate(`(()=>{const r=document.getElementById('game-fullscreen').getBoundingClientRect();return Math.abs(r.x-${pinnedCorner.x})<1&&Math.abs(r.y-${pinnedCorner.y})<1})()`))throw Error('Fullscreen icon migrated while panning');

 if(!await evaluate(`getComputedStyle(document.getElementById('board-viewport')).overflow==='hidden'&&getComputedStyle(document.getElementById('board-viewport')).touchAction==='none'`))throw Error('Zoom still uses native scrolling');
 await evaluate(`document.getElementById('board-viewport').scrollTo(0,0)`);
 await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
 await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:dragPoint.x,y:dragPoint.y}]});
 await send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:dragPoint.x-70,y:dragPoint.y-70}]});
 await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
 if(!await evaluate(`document.getElementById('board-viewport').scrollTop>=60&&document.getElementById('board-viewport').scrollLeft>=60&&!document.querySelector('.map-panning')&&sent.length===0`))throw Error('Finger drag did not pan the map');
 await send('Emulation.setTouchEmulationEnabled',{enabled:false});
 await send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,x:dragPoint.x-70,y:dragPoint.y-70});
 await send('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,...dragPoint});
 await send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...dragPoint});
 if(!await evaluate(`document.getElementById('board-viewport').scrollLeft<=1&&document.getElementById('board-viewport').scrollTop<=1`))throw Error('Reverse dragging did not pan right and down');


 await evaluate(`document.querySelector('[aria-label="Reset 2D zoom to fit board"]').click()`);await pause(150);
 if(!await evaluate(`Math.abs(document.getElementById('board').getBoundingClientRect().width-${initialMapWidth})<1`))throw Error('Fit did not reset map zoom');

 await evaluate(`document.getElementById('view-follow').click();document.getElementById('view-full').click()`);await pause(350);
 if(!await evaluate(`document.getElementById('board-viewport').dataset.render==='flat'&&!document.querySelector('.board-world')`))throw Error('Returning to map during loading created a scene');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="3d" && calls>0');
 if(!await evaluate(`document.getElementById('board-viewport').dataset.view==='follow'&&getComputedStyle(document.getElementById('board')).visibility==='hidden'&&document.getElementById('view-follow').getAttribute('aria-pressed')==='true'`))throw Error('Follow did not open 3D');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="flat"');await pause(100);
 const stopped=await evaluate('calls');await pause(200);if(await evaluate('calls')!==stopped)throw Error('Full map continued rendering 3D');
 if(!await evaluate(`document.querySelector('.board-world').hidden&&!document.getElementById('game-map-toggle').hidden&&document.getElementById('game-reset-view').hidden&&document.querySelectorAll('.mm-sq').length===40&&getComputedStyle(document.querySelector('.mm-center')).backgroundColor==='rgb(35, 61, 96)'&&document.querySelectorAll('.mm-deck-zone').length===2`))throw Error('Full board or theme incorrect');
 if(await evaluate('JSON.stringify(state)')!==snapshot||await evaluate('sent.length')!==0)throw Error('Changing view changed gameplay');
 console.log('PASS: Full Board is themed 2D, Follow Token is 3D, no graphics toggle, safe loading race, and hidden 3D stops drawing');
 await send('Page.navigate',{url:origin+'/'});await waitFor('!!document.getElementById("create")');await evaluate(setup);
 if(!await evaluate(`document.getElementById('board-viewport').dataset.render==='flat'&&!document.querySelector('.board-world')`))throw Error('Full map preference lost on reload');
 await evaluate(`state={...state,phase:'MOVING',pending:{kind:'move',player:'p1',from:0,to:3},last_dice:null};update();document.getElementById('view-follow').click()`);
 await waitFor('document.getElementById("board-viewport").dataset.render==="3d"');await evaluate('document.getElementById("view-full").click()');
 await waitFor('!board.hasMotion()');
 if(!await evaluate(`document.querySelector('.mm-token[data-player="p1"]').dataset.square==='3'`))throw Error('Switching interrupted token movement');
 await evaluate(`state={...state,phase:'WAITING_FOR_DECISION',players:state.players.map(p=>p.id==='p1'?{...p,position:3}:p),pending:{kind:'decision',type:'buy',player:'p1',square:3,price:60}};update()`);
 await waitFor('!document.getElementById("turn-card").hidden');if(!await evaluate(`getComputedStyle(document.querySelector('.mm-deck-chest')).opacity==='1'`))throw Error('Chest fades behind action card');await evaluate('document.getElementById("turn-buy").click()');
 if(!await evaluate(`sent.length===1&&sent[0].action==='decision'&&sent[0].answer==='yes'`))throw Error('Flat board action changed');
 await evaluate('state={...state,last_roll:11};update()');
 await send('Runtime.evaluate',{expression:'document.getElementById("game-fullscreen").click()',userGesture:true});await waitFor('!!document.fullscreenElement');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="3d"');
 if(!await evaluate(`(()=>{const buttons=['game-map-toggle','game-reset-view','game-players','game-options'].map(id=>document.getElementById(id));const tools=document.querySelector('.game-surface-tools').getBoundingClientRect();return buttons.every(b=>b.checkVisibility())&&tools.top<30})()`))throw Error('3D fullscreen lost its top controls');
 await evaluate('document.getElementById("game-map-toggle").click()');await waitFor('document.getElementById("board-viewport").dataset.render==="flat"');
 if(!await evaluate(`(()=>{const total=document.getElementById('last-roll-badge'),map=document.getElementById('board').getBoundingClientRect(),r=total.getBoundingClientRect();return [...document.querySelectorAll('.game-surface-tools > .btn,.game-surface-tools > .map-zoom-controls')].every(el=>getComputedStyle(el).display==='none')&&r.left>map.right&&Math.abs(r.top-map.top)<2&&!document.getElementById('game-fullscreen').hidden})()`))throw Error('Fullscreen controls are not simplified or last roll overlaps the map');
 if(!await evaluate(`(()=>{const page=getComputedStyle(document.body),full=getComputedStyle(document.fullscreenElement);return full.backgroundColor===page.backgroundColor&&full.backgroundImage===page.backgroundImage&&full.backgroundImage.includes('repeating-linear-gradient')})()`))throw Error('2D fullscreen lost the wood background');

 if(!await evaluate(`document.getElementById('game-fullscreen').getAttribute('aria-label')==='Exit fullscreen'&&!!document.querySelector('#game-fullscreen svg')`))throw Error('Fullscreen icon did not switch to exit');
 for(const [width,height] of [[1440,900],[390,844]]){
   await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<600});await pause(150);
   if(!await evaluate(`(()=>{const b=document.getElementById('board').getBoundingClientRect(),r=document.getElementById('last-roll-badge').getBoundingClientRect();return Math.abs(b.left+b.width/2-innerWidth/2)<2&&Math.abs(b.top+b.height/2-innerHeight/2)<2&&(r.left>=b.right||r.bottom<=b.top)&&r.right<=innerWidth})()`))throw Error('Fullscreen board is off center or roll badge overlaps at '+width);
 }
 await send('Emulation.setDeviceMetricsOverride',{width:1366,height:900,deviceScaleFactor:1,mobile:false});await pause(100);

 await fs.writeFile('/private/tmp/magic-monopoly-flat-board.png',Buffer.from((await send('Page.captureScreenshot',{format:'png'})).data,'base64'));
 await evaluate('document.getElementById("game-fullscreen").click()');await waitFor('!document.fullscreenElement');
 console.log('PASS: Map preference survives refresh, switching preserves movement, 2D decisions work, and fullscreen includes both views');
 for(const [width,height] of [[390,844],[320,720]]){
   await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:true});await pause(100);
   const fits=()=>evaluate(`(()=>{const v=document.getElementById('board-viewport').getBoundingClientRect(),tools=document.querySelector('.game-surface-tools').getBoundingClientRect();return tools.left>=v.left-1&&tools.right<=v.right+1})()`);
   if(!await fits())throw Error('Map toolbar overflows at '+width);
   if(!await evaluate(`(()=>{const button=document.getElementById('game-fullscreen'),b=button.getBoundingClientRect(),icon=button.querySelector('svg').getBoundingClientRect();return b.width>=24&&b.width<=32&&icon.width<b.width&&Math.abs(b.width-b.height)<1})()`))throw Error('Fullscreen icon does not scale down for mobile');

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
