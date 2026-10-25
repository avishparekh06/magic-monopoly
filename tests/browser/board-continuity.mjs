import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
// Run with the local server running: node tests/browser/board-continuity.mjs [origin]
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
 await send('Page.bringToFront');await send('Emulation.setFocusEmulationEnabled',{enabled:true});
 const check=await evaluate(`(async()=>{
   const T=await import('/static/vendor/three/three.module.min.js');
   const {createBoardView,loadBoard}=await import('/static/board.js');
   const {createBoardWorld}=await import('/static/board-world.js');
   const data=await loadBoard();await document.fonts.ready;
   document.body.replaceChildren();
   const viewport=document.createElement('div');viewport.style.cssText='position:relative;width:480px;height:360px';
   const board=document.createElement('div');board.id='board';board.style.width='480px';viewport.append(board);document.body.append(viewport);
   const realObserver=window.ResizeObserver,observers=[];
   window.ResizeObserver=class{constructor(callback){this.callback=callback;observers.push(this)}observe(){}unobserve(){}disconnect(){}};
   const view=createBoardView(board,data),state={phase:'WAITING_FOR_ROLL',owners:{},players:[{id:'p1',name:'Grandma',position:8,lane:1},{id:'p2',name:'Maya',position:28,lane:2}]};view.render(state);
   const originalLook=T.Object3D.prototype.lookAt,originalAdd=T.Object3D.prototype.add;
   const widthDescriptor=Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,'width'),heightDescriptor=Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype,'height'),hiddenDescriptor=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'hidden');
   let camera=null,drawn=0,resets=0,prematureReveal=false;
   T.Object3D.prototype.lookAt=function(...args){if(this.isPerspectiveCamera)camera=this;return originalLook.apply(this,args)};
   T.Object3D.prototype.add=function(...children){if(this.isScene)this.onAfterRender=()=>drawn++;return originalAdd.apply(this,children)};
   for(const [key,descriptor] of [['width',widthDescriptor],['height',heightDescriptor]])Object.defineProperty(HTMLCanvasElement.prototype,key,{...descriptor,set(value){if(this.classList.contains('board-world'))resets++;return descriptor.set.call(this,value)}});
   Object.defineProperty(HTMLElement.prototype,'hidden',{...hiddenDescriptor,set(value){if(this.classList.contains('board-world')&&!value&&!drawn)prematureReveal=true;return hiddenDescriptor.set.call(this,value)}});
   const realRAF=window.requestAnimationFrame,realCAF=window.cancelAnimationFrame,realNow=performance.now.bind(performance),callbacks=new Map();let clock=0,seq=0;
   window.requestAnimationFrame=fn=>{const id=++seq;callbacks.set(id,fn);return id};window.cancelAnimationFrame=id=>callbacks.delete(id);Object.defineProperty(performance,'now',{configurable:true,value:()=>clock});
   const tick=()=>{clock+=16;const pending=[...callbacks.values()];callbacks.clear();pending.forEach(fn=>fn(clock))};
   let world;
   try {
     world=createBoardWorld(viewport,data,()=>{throw Error('WebGL unavailable')});world.updateState(state);world.focus('p1');world.setMode('follow');world.setVisible(true);
     const drawnBeforeReveal=drawn>0&&!prematureReveal;
     const resize=observers.at(-1).callback,beforeSame=resets;
     for(let i=0;i<5;i++){resize();tick()}
     const unchangedResets=resets-beforeSame;
     const beforeResize=drawn;viewport.style.width='520px';viewport.style.height='380px';resize();const synchronousResize=drawn>beforeResize;
     const beforeResume=drawn;world.setVisible(false);world.setVisible(true);const synchronousResume=drawn>beforeResume;
     let minimumCameraRadius=Infinity,maxStep=0;
     for(const id of ['p2','p1','p2']){
       world.focus(id);let prior=camera.position.clone();
       for(let i=0;i<230;i++){tick();minimumCameraRadius=Math.min(minimumCameraRadius,Math.hypot(camera.position.x,camera.position.z));maxStep=Math.max(maxStep,camera.position.distanceTo(prior));prior.copy(camera.position)}
     }
     const oldToken=board.querySelector('.mm-token[data-player="p1"]'),layoutBefore=oldToken.dataset.layoutWidth;
     board.style.width='320px';world.focus('p1');world.resetView();tick();
     const beforeLayout=camera.position.clone();observers[0].callback();world.resetView();tick();
     const resizePositionError=camera.position.distanceTo(beforeLayout);
     return {drawnBeforeReveal,unchangedResets,synchronousResize,synchronousResume,minimumCameraRadius,maxStep,layoutBefore,resizePositionError,frames:drawn};
   } finally {
     world?.setVisible(false);window.ResizeObserver=realObserver;window.requestAnimationFrame=realRAF;window.cancelAnimationFrame=realCAF;Object.defineProperty(performance,'now',{configurable:true,value:realNow});
     T.Object3D.prototype.add=originalAdd;T.Object3D.prototype.lookAt=originalLook;
     Object.defineProperty(HTMLCanvasElement.prototype,'width',widthDescriptor);Object.defineProperty(HTMLCanvasElement.prototype,'height',heightDescriptor);Object.defineProperty(HTMLElement.prototype,'hidden',hiddenDescriptor);
   }
 })()`);
 if(!check.drawnBeforeReveal||check.unchangedResets!==0||!check.synchronousResize||!check.synchronousResume||check.minimumCameraRadius<25||check.maxStep>1.8||check.resizePositionError>.3)throw Error('Board blackout regression '+JSON.stringify(check));
 console.log('PASS: Turn handoffs stay outside scenery; frames render before reveal/resume/resize; unchanged sizes never clear the canvas '+JSON.stringify(check));
}catch(e){
 console.error(e);
 if(ws?.readyState===1){
  ws.send(JSON.stringify({id:9999,method:'Runtime.evaluate',params:{expression:'document.body.innerText',returnByValue:true}}));
  ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id===9999)console.error('Rendered page:',m.result?.result?.value?.slice(0,1500));};
  await pause(500);
 }
 process.exitCode=1;
}finally{ws?.close();chrome.kill();}
