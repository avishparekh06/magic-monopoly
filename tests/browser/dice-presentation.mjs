import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
// Run with the local server running: node tests/browser/dice-presentation.mjs [origin]
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

 await send('Runtime.enable');await send('Page.navigate',{url:origin+'/' });await waitFor('!!document.getElementById("create")');
 const check=await evaluate(`(async()=>{
   const T=await import('/static/vendor/three/three.module.min.js');const {createDiceThrow,DICE_TOSS_MS}=await import('/static/board-dice.js');
   const scene=new T.Scene(),toss=createDiceThrow(scene,false),p=new T.Vector3();let min=Infinity;
   for(let face=1;face<=6;face++){
     toss.start([face,7-face],{x:15,z:19},0);
     for(let ms=0;ms<=DICE_TOSS_MS;ms+=25){
       toss.update(ms);scene.updateMatrixWorld(true);
       for(const mesh of scene.children){const a=mesh.geometry.attributes.position;for(let i=0;i<a.count;i++){p.fromBufferAttribute(a,i).applyMatrix4(mesh.matrixWorld);min=Math.min(min,p.y);}}
     }
   }
   toss.clear();return {min,hidden:scene.children.every(mesh=>!mesh.visible)};
 })()`);
 if(check.min<.31999||!check.hidden)throw Error('Dice clipping/cleanup failed: '+JSON.stringify(check));
 console.log('PASS: Dice stay above the board through every sampled bounce for all six faces; physical-input cleanup hides old dice');
 const presentation=await evaluate(`(async()=>{
   const T=await import('/static/vendor/three/three.module.min.js');
   const {createDiceThrow,DICE_TOSS_MS,DICE_SIZE,dicePath}=await import('/static/board-dice.js');
   const scene=new T.Scene(),toss=createDiceThrow(scene,false),origin={x:14,z:19};
   toss.start([2,5],origin,0);toss.update(20);const first=scene.children[0].position.clone();
   toss.update(80);const shake=first.distanceTo(scene.children[0].position);
   toss.update(1650);const end=scene.children[0].position.clone();
   const travel=dicePath(origin,0,0).distanceTo(dicePath(origin,0,1));
   const camera=new T.PerspectiveCamera(46,1,.1,100);camera.position.set(end.x,9,end.z+12);camera.lookAt(end);camera.updateProjectionMatrix();
   toss.avoidTokens(camera,[new T.Vector3(end.x,.34,end.z)]);const protectedToken=!scene.children[0].visible;
   toss.update(2400);const cleared=scene.children.every(m=>!m.visible);
   const reducedScene=new T.Scene(),reduced=createDiceThrow(reducedScene,true);reduced.start([1,6],origin,0);reduced.update(0);
   const reducedAtRest=Math.abs(reducedScene.children[0].position.y-(.32+DICE_SIZE/2))<.01;
   const {mountBoardActions}=await import('/static/board-actions.js');
   document.body.replaceChildren();
   for(const href of ['/static/style.css','/static/typography.css','/static/board-theme.css']) {const link=document.createElement('link');link.rel='stylesheet';link.href=href;document.head.append(link)}
   for(const id of ['view-follow','view-full']){const b=document.createElement('button');b.id=id;document.body.append(b)}
   const viewport=document.createElement('div');viewport.className='board-viewport';viewport.style.cssText='width:900px;height:650px';viewport.dataset.view='follow';viewport.dataset.render='flat';document.body.append(viewport);
   const board=await(await fetch('/api/board')).json(),cards=await(await fetch('/api/cards')).json();
   const sent=[];const actions=mountBoardActions(viewport,board,cards,a=>sent.push(a));
   const state={game_id:'fixture',phase:'WAITING_FOR_ROLL',turn:1,current_player:'p1',pending:null,owners:{},players:[{id:'p1',name:'Test',kind:'in_person',position:0,lane:1,balance:1500,properties:[],connected:true}]};
   actions.update(state);actions.connection('online');
   await new Promise(r=>setTimeout(r,150));
   const button=document.getElementById('turn-roll'),rect=button.getBoundingClientRect();
   button.click();const instantHidden=button.hidden;button.click();
   const singleRequest=sent.length===1&&sent[0].action==='board_roll';
   actions.error('Retry');const retryVisible=!button.hidden;
   return {shake,travel,protectedToken,cleared,reducedAtRest,width:rect.width,height:rect.height,instantHidden,singleRequest,retryVisible};
 })()`);
 if(presentation.shake<.01||presentation.travel>.2||!presentation.protectedToken||!presentation.cleared||!presentation.reducedAtRest||presentation.width>190||presentation.height>65||!presentation.instantHidden||!presentation.singleRequest||!presentation.retryVisible)throw Error('Presentation check failed '+JSON.stringify(presentation));
 console.log('PASS: Shake, contained tumble, token visibility guard, dice cleanup and reduced motion '+JSON.stringify(presentation));
 console.log('PASS: Compact roll button hides synchronously, blocks duplicate rolls, and allows retry on error');
 const sequence=await evaluate(`(async()=>{
   const {createBoardView,loadBoard}=await import('/static/board.js');
   const {TOKEN_STEP_MS}=await import('/static/board-motion.js');
   const data=await loadBoard(),el=document.createElement('div');el.style.width='800px';document.body.append(el);
   const view=createBoardView(el,data),player={id:'p1',name:'Test',position:0,lane:1};
   const ready={phase:'WAITING_FOR_ROLL',players:[player],owners:{},pending:null,last_dice:null};
   view.render(ready);
   const realTimeout=window.setTimeout,realNow=performance.now.bind(performance);
   let clock=0,seq=0;const timers=new Map();
   window.setTimeout=(fn,delay=0)=>{const id=++seq;timers.set(id,{at:clock+delay,fn});return id};
   Object.defineProperty(performance,'now',{configurable:true,value:()=>clock});
   const advance=to=>{let steps=0;while(true){const next=[...timers.entries()].filter(([,t])=>t.at<=to).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;if(++steps>1000)throw Error('Timer loop');timers.delete(next[0]);clock=next[1].at;next[1].fn()}clock=to};
   const square=()=>el.querySelector('.mm-token').dataset.square;
   try {
     const moving={...ready,phase:'MOVING',last_dice:[1,1],pending:{kind:'move',player:'p1',from:0,to:2}};
     view.render(moving);advance(500);const heldBeforeEvent=square()==='0';
     view.onEvent({type:'rolled',player:'p1',dice:[1,1]});advance(1200);
     el.dispatchEvent(new CustomEvent('board-dice-start',{detail:{player:'p1'}}));
     // Engine completion may arrive while the visual dice are still rolling.
     view.render({...moving,phase:'WAITING_FOR_DECISION',players:[{...player,position:2}],pending:{kind:'decision',player:'p1'}});
     advance(3599);const heldThroughToss=square()==='0';advance(3600);const startsAfterToss=square()==='1' && Number(el.querySelector('.mm-token').dataset.hopDuration)>=500;
     advance(3600+2*TOKEN_STEP_MS+100);const lands=square()==='2'&&!view.hasMotion();
     const physicalStart=clock;
     view.render({...ready,players:[{...player,position:2}]});
     view.render({...moving,last_dice:null,players:[{...player,position:2}],pending:{kind:'move',player:'p1',from:2,to:4}});
     view.onEvent({type:'rolled',player:'p1',dice:null});advance(physicalStart);const physicalImmediate=square()==='3';advance(physicalStart+2*TOKEN_STEP_MS+100);
     const cardStart=clock;
     view.render({...moving,last_dice:null,players:[{...player,position:4}],pending:{kind:'move',player:'p1',from:4,to:1,no_salary:true}});
     advance(cardStart);const cardImmediate=square()==='3';advance(cardStart+3*TOKEN_STEP_MS+100);
     const stalledStart=clock;
     view.render({...moving,last_dice:null,players:[{...player,position:1}],pending:{kind:'move',player:'p1',from:1,to:8}});
     advance(stalledStart);view.setStalled(true);
     view.render({...moving,last_dice:null,players:[{...player,position:1}],pending:{kind:'move',player:'p1',from:1,to:8}});
     advance(stalledStart+1000);const stalledStopped=square()==='1'&&!view.hasMotion();
     view.setStalled(false);
     view.render({...moving,last_dice:null,players:[{...player,position:1}],pending:{kind:'move',player:'p1',from:1,to:8}});
     advance(stalledStart+1000);const retryMoves=square()==='2';
     return {heldBeforeEvent,heldThroughToss,startsAfterToss,lands,physicalImmediate,cardImmediate,stalledStopped,retryMoves};
   } finally {window.setTimeout=realTimeout;Object.defineProperty(performance,'now',{configurable:true,value:realNow})}
 })()`);
 if(Object.values(sequence).some(value=>!value))throw Error('Roll/movement sequencing failed '+JSON.stringify(sequence));
 console.log('PASS: Token waits for delayed roll events and actual 3D start; early engine completion cannot move it; physical rolls and card moves stay immediate');
 const flatDice=await evaluate(`(async()=>{
   const {createBoardView,loadBoard}=await import('/static/board.js'),{mountBoardActions}=await import('/static/board-actions.js');
   const data=await loadBoard(),cards=await(await fetch('/api/cards')).json();document.body.replaceChildren();
   for(const id of ['view-follow','view-full']){const button=document.createElement('button');button.id=id;document.body.append(button)}
   const viewport=document.createElement('div');viewport.style.cssText='position:relative;width:800px;height:800px';viewport.dataset.render='flat';viewport.dataset.view='full';document.body.append(viewport);
   const el=document.createElement('div');el.id='board';el.style.width='800px';viewport.append(el);const board=createBoardView(el,data);
   const state={game_id:'fixture',phase:'MOVING',turn:1,current_player:'p1',pending:{kind:'move',player:'p1',from:0,to:1},owners:{},players:[{id:'p1',name:'Test',position:0,lane:1,kind:'in_person',balance:1500,properties:[]}]};
   board.render({...state,phase:'WAITING_FOR_ROLL',pending:null});const actions=mountBoardActions(viewport,data,cards,()=>{});let outside=0;
   for(let square=0;square<40;square++){
     state.pending.from=square;actions.update(state);actions.event({type:'rolled',player:'p1',dice:[3,4],value:7});
     const cell=el.querySelectorAll('.mm-sq')[square].getBoundingClientRect(),dice=viewport.querySelectorAll('.game-fallback-die');
     for(const time of [0,200,600,1000,1500])for(const die of dice){
       for(const animation of die.getAnimations()){animation.pause();animation.currentTime=time}
       const rect=die.getBoundingClientRect();if(rect.left<cell.left-.5||rect.right>cell.right+.5||rect.top<cell.top-.5||rect.bottom>cell.bottom+.5)outside++;
     }
   }
   return {outside,count:viewport.querySelectorAll('.game-fallback-die').length,square:viewport.querySelector('.game-dice-fallback').dataset.square};
 })()`);
 if(flatDice.outside||flatDice.count!==2||flatDice.square!=='39')throw Error('Flat dice escaped the starting square '+JSON.stringify(flatDice));
 console.log('PASS: Flat-board dice stay inside all 40 starting squares through every sampled animation phase');

}catch(e){
 console.error(e);
 if(ws?.readyState===1){
  ws.send(JSON.stringify({id:9999,method:'Runtime.evaluate',params:{expression:'document.body.innerText',returnByValue:true}}));
  ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id===9999)console.error('Rendered page:',m.result?.result?.value?.slice(0,1500));};
  await pause(500);
 }
 process.exitCode=1;
}finally{ws?.close();chrome.kill();}
