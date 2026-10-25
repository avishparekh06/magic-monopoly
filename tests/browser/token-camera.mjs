import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
// Run with the local server running: node tests/browser/token-camera.mjs [origin]
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
   const {TOKEN_STEP_MS,TOKEN_LANDING_MS}=await import('/static/board-motion.js');
   const data=await loadBoard();await document.fonts.ready;
   const viewport=document.createElement('div');viewport.style.cssText='position:relative;width:480px;height:360px';
   const board=document.createElement('div');board.id='board';board.style.width='480px';viewport.append(board);document.body.replaceChildren(viewport);
   const view=createBoardView(board,data);
   const state={phase:'MOVING',owners:{},players:[{id:'p1',name:'Test',position:8,lane:1}]};view.render(state);
   const token=board.querySelector('.mm-token');
   const originalAdd=T.Object3D.prototype.add,originalLook=T.Object3D.prototype.lookAt;
   let piece=null,camera=null,frameAim=null,sceneRef=null;const diceMeshes=[];
   T.Object3D.prototype.add=function(...children){if(this.isScene)sceneRef=this;if(this.isScene&&diceMeshes.length<2)for(const c of children)if(c.isMesh&&diceMeshes.length<2)diceMeshes.push(c);if(children.some(c=>c.isSprite))piece=this;return originalAdd.apply(this,children)};
   T.Object3D.prototype.lookAt=function(...args){if(this.isPerspectiveCamera){camera=this;frameAim=args[0].isVector3?args[0].clone():new T.Vector3(...args)}return originalLook.apply(this,args)};
   const realRAF=window.requestAnimationFrame,realCAF=window.cancelAnimationFrame,realNow=performance.now.bind(performance);
   const callbacks=new Map();let clock=0,sequence=0;const samples=[];let rollAt=null,rollCameraPose=null,rollNumber=0;
   board.addEventListener('board-dice-start',()=>{rollAt=clock;rollCameraPose=camera?.position.clone();rollNumber++});
   window.requestAnimationFrame=fn=>{const id=++sequence;callbacks.set(id,fn);return id};window.cancelAnimationFrame=id=>callbacks.delete(id);
   Object.defineProperty(performance,'now',{configurable:true,value:()=>clock});
   let world;
   const tick=ms=>{clock=ms;const pending=[...callbacks.values()];callbacks.clear();pending.forEach(fn=>fn(ms));if(camera&&piece)samples.push({camera:camera.position.clone(),aim:frameAim.clone(),height:piece.position.y})};
   const xy=index=>{token.dataset.square=String(index);token.dataset.layoutWidth=String(board.clientWidth);token.dataset.layoutHeight=String(board.clientHeight);const cell=board.querySelectorAll('.mm-sq')[index];return {x:cell.offsetLeft+cell.offsetWidth*.23-parseFloat(token.style.width)/2,y:cell.offsetTop+cell.offsetHeight*.74-parseFloat(token.style.height)/2}};
   try {
     world=createBoardWorld(viewport,data,()=>{throw Error('WebGL unavailable')});world.updateState(state);world.focus('p1');world.setMode('follow');
     world.rollDice({player:'p1',from:8,dice:[1,6]});world.setVisible(true);tick(0);tick(1650);
     const earlyRollVisible=diceMeshes.every(mesh=>{const projected=mesh.position.clone().project(camera);return mesh.visible&&Math.abs(projected.x)<.97&&Math.abs(projected.y)<.97});
     world.clearDice();for(let i=0;i<30;i++)tick(clock+16);
     const rollCamera=camera.position.clone(),rollAim=frameAim.clone();let rollDrift=0,rollAimDrift=0;
     world.rollDice({player:'p1',from:8,dice:[3,4]});const rollStart=clock;
     for(let elapsed=0;elapsed<=3000;elapsed+=50){tick(rollStart+elapsed);rollDrift=Math.max(rollDrift,camera.position.distanceTo(rollCamera));rollAimDrift=Math.max(rollAimDrift,frameAim.distanceTo(rollAim))}
     const across=xy(22);token.style.left=across.x+'px';token.style.top=across.y+'px';tick(clock+16);
     const beforeHandoff=rollNumber;world.rollDice({player:'p1',from:22,dice:[4,3]});const handoffStart=clock;let handoffRollDrift=0;
     for(let elapsed=0;elapsed<=7000;elapsed+=50){tick(handoffStart+elapsed);if(rollNumber>beforeHandoff){if(clock-rollAt>=2400)break;handoffRollDrift=Math.max(handoffRollDrift,camera.position.distanceTo(rollCameraPose))}}
     world.clearDice();const home=xy(8);token.style.left=home.x+'px';token.style.top=home.y+'px';world.resetView();tick(clock+50);
     samples.length=0;
     let from=8;
     for(const to of [9,10,11,12]){
       const start=clock+16,a=xy(from),b=xy(to),duration=TOKEN_STEP_MS-TOKEN_LANDING_MS;
       token.style.transition='none';token.dataset.hopStart=String(start);token.dataset.hopDuration=String(duration);
       for(let elapsed=0;elapsed<=TOKEN_STEP_MS;elapsed+=16){
         const u=Math.min(1,elapsed/duration),ease=u*u*(3-2*u);
         token.style.left=(a.x+(b.x-a.x)*ease)+'px';token.style.top=(a.y+(b.y-a.y)*ease)+'px';tick(start+elapsed);
       }
       from=to;
     }
     const y=samples.map(p=>p.camera.y),aimY=samples.map(p=>p.aim.y),heights=samples.map(p=>p.height);
     const cameraYRange=Math.max(...y)-Math.min(...y),aimYRange=Math.max(...aimY)-Math.min(...aimY);
     const maxCameraStep=Math.max(...samples.slice(1).map((p,i)=>p.camera.distanceTo(samples[i].camera)));
     world.setMode('full');tick(clock+16);const fullCamera=camera.position.clone();let overviewDrift=0;
     world.rollDice({player:'p1',from:12,dice:[2,5]});const fullRollStart=clock;
     for(let elapsed=0;elapsed<=3000;elapsed+=50){token.dataset.hopStart=String(clock-80);tick(fullRollStart+elapsed);overviewDrift=Math.max(overviewDrift,camera.position.distanceTo(fullCamera))}
     world.canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight'}));tick(clock+50);
     const chosenView=camera.position.clone(),idleStart=clock;let idleDrift=0;
     for(let elapsed=50;elapsed<=7000;elapsed+=50){tick(idleStart+elapsed);idleDrift=Math.max(idleDrift,camera.position.distanceTo(chosenView))}
     const invisible=[],escapedSquare=[];
     const floors=[];sceneRef.traverse(object=>{if(object.isMesh&&object.geometry.type==='PlaneGeometry'&&Math.abs(object.position.y-.319)<.001&&object.material.map)floors.push(object)});
     let retrievalCameraDrift=0;
     for(const [width,height] of [[480,360],[320,470]]){
       viewport.style.width=width+'px';viewport.style.height=height+'px';board.style.width=width+'px';world.setVisible(false);world.setVisible(true);world.setMode('follow');
       for(let square=0;square<40;square++){
         const pos=xy(square);token.style.transition='none';token.style.left=pos.x+'px';token.style.top=pos.y+'px';token.dataset.hopDuration='0';
         state.players[0].position=square;world.updateState(state);world.resetView();tick(clock+50);
         const fixedCamera=camera.position.clone();world.rollDice({player:'p1',from:square,dice:[3,4]});const started=clock;
         const floorBox=new T.Box3().setFromObject(floors[square]);
         for(const elapsed of [0,160,400,750,1100,1400,1650]){
           tick(started+elapsed);retrievalCameraDrift=Math.max(retrievalCameraDrift,camera.position.distanceTo(fixedCamera));
           for(const mesh of diceMeshes){
             mesh.updateMatrixWorld(true);const box=new T.Box3().setFromObject(mesh,true);
             if(box.min.x<floorBox.min.x-.09||box.max.x>floorBox.max.x+.09||box.min.z<floorBox.min.z-.09||box.max.z>floorBox.max.z+.09||box.min.y<.31999||box.max.y>2.5)escapedSquare.push({width,square,elapsed});
           }
         }
         const visible=diceMeshes.filter(mesh=>{const projected=mesh.position.clone().project(camera);return mesh.visible&&Math.abs(projected.x)<.97&&Math.abs(projected.y)<.97&&projected.z>-1&&projected.z<1}).length;
         if(visible!==2)invisible.push({width,square,visible,dice:diceMeshes.map(mesh=>({visible:mesh.visible,position:mesh.position.toArray(),screen:mesh.position.clone().project(camera).toArray()}))});
         world.clearDice();
       }
     }
     return {cameraYRange,aimYRange,maxCameraStep,peakHop:Math.max(...heights),landingHeight:heights.at(-1),overviewDrift,rollDrift,rollAimDrift,earlyRollVisible,handoffRollDrift,idleDrift,invisible,escapedSquare,retrievalCameraDrift,stepMs:TOKEN_STEP_MS,frames:samples.length};
   } finally {
     world?.setVisible(false);window.requestAnimationFrame=realRAF;window.cancelAnimationFrame=realCAF;Object.defineProperty(performance,'now',{configurable:true,value:realNow});T.Object3D.prototype.add=originalAdd;T.Object3D.prototype.lookAt=originalLook;
   }
 })()`);
 if(!check.earlyRollVisible||check.handoffRollDrift>1e-7||check.rollDrift>1e-7||check.rollAimDrift>1e-7||check.idleDrift>1e-7||check.invisible.length||check.escapedSquare.length||check.retrievalCameraDrift>1e-7||check.cameraYRange>1e-7||check.aimYRange>1e-7||check.overviewDrift>1e-7||check.maxCameraStep>.55||check.peakHop<.7||check.peakHop>.82||Math.abs(check.landingHeight-.34)>.01||check.stepMs<500||check.frames<100)throw Error('Camera/token smoothness failed '+JSON.stringify(check));
 console.log('PASS: No dice cutaway, no idle recentering, steady hops and corner following; both dice stay inside their starting square through the entire roll, remain visible, and reset without moving the camera in landscape and portrait '+JSON.stringify(check));
}catch(e){
 console.error(e);
 if(ws?.readyState===1){
  ws.send(JSON.stringify({id:9999,method:'Runtime.evaluate',params:{expression:'document.body.innerText',returnByValue:true}}));
  ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id===9999)console.error('Rendered page:',m.result?.result?.value?.slice(0,1500));};
  await pause(500);
 }
 process.exitCode=1;
}finally{ws?.close();chrome.kill();}
