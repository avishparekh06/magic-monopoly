// A miniature fairy-tale world. This module only reads game state and the existing
// token animation; it never sends an action or changes a rule, position, or balance.
import * as THREE from '/static/vendor/three/three.module.min.js';
import { tokenGlyph } from '/static/net.js';
import { createDiceThrow, dicePath } from '/static/board-dice.js';

const SIZE = 44, HALF = SIZE / 2;
const COLORS = { cream: '#f6e7c8', wood: '#84502f', edge: '#422b25', gold: '#dbb15c', grass: '#478464', roof: '#873f6a', stone: '#d6cbbe', light: '#ffd58c' };
const PLAYER = { p1: '#8d3869', p2: '#397f94', p3: '#bf9138' };
const weights = [1.6, ...Array(9).fill(1), 1.6];
const edges = [-HALF];
for (const weight of weights) edges.push(edges.at(-1) + weight * SIZE / 12.2);
function cell(index) {
  const row = index <= 10 ? 10 : index <= 20 ? 20 - index : index <= 30 ? 0 : index - 30;
  const col = index <= 10 ? 10 - index : index <= 20 ? 0 : index <= 30 ? index - 20 : 10;
  const x = (edges[col] + edges[col + 1]) / 2, z = (edges[row] + edges[row + 1]) / 2;
  let nx = 0, nz = 0;
  if (row === 10) nz = 1; else if (row === 0) nz = -1;
  if (col === 0) nx = -1; else if (col === 10) nx = 1;
  const length = Math.hypot(nx, nz);
  return { x, z, w: edges[col + 1] - edges[col], d: edges[row + 1] - edges[row], nx: nx / length, nz: nz / length };
}

// Fit the board base and tallest scenery into both camera axes, including portrait.
// The close-up and overview share every mesh, material, token and ownership marker.
export function overviewPose(aspect, yaw = 0) {
  const target = new THREE.Vector3(0, 2, 0);
  const outward = new THREE.Vector3(Math.sin(yaw) * .38, .925, Math.cos(yaw) * .38).normalize();
  const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), outward).normalize();
  const up = new THREE.Vector3().crossVectors(outward, right);
  const tanY = Math.tan(THREE.MathUtils.degToRad(46 / 2)), tanX = tanY * Math.max(aspect, .1);
  let distance = 0;
  for (const x of [-23, 23]) for (const y of [-3, 14]) for (const z of [-23, 23]) {
    const corner = new THREE.Vector3(x, y, z).sub(target);
    distance = Math.max(distance, corner.dot(outward) + Math.max(Math.abs(corner.dot(right)) / tanX, Math.abs(corner.dot(up)) / tanY));
  }
  return { target, position: outward.multiplyScalar(distance * 1.12).add(target) };
}

// Follow the perimeter rather than cutting a chord through the central scenery.
function dampAroundBoard(current, target, amount, maxAngleDelta) {
  const radius = Math.hypot(current.x, current.z), targetRadius = Math.hypot(target.x, target.z);
  if (radius < .001 || targetRadius < .001) { current.lerp(target, amount); return; }
  const angle = Math.atan2(current.x, current.z), targetAngle = Math.atan2(target.x, target.z);
  const delta = Math.atan2(Math.sin(targetAngle - angle), Math.cos(targetAngle - angle));
  const turn = THREE.MathUtils.clamp(delta * amount, -maxAngleDelta, maxAngleDelta);
  const nextAngle = angle + turn, nextRadius = radius + (targetRadius - radius) * amount;
  current.set(Math.sin(nextAngle) * nextRadius, current.y + (target.y - current.y) * amount, Math.cos(nextAngle) * nextRadius);
}

export function createBoardWorld(viewport, boardData, onUnavailable) {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.12;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  const canvas = renderer.domElement;
  canvas.className = 'board-world'; canvas.tabIndex = 0; canvas.hidden = true;
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', '3D storybook board. Drag or use left and right arrows to look around. Home resets the camera.');
  canvas.addEventListener('webglcontextlost', e => { e.preventDefault(); setVisible(false); onUnavailable(); });
  viewport.append(canvas);
  const scene = new THREE.Scene();
  const diceThrow = createDiceThrow(scene, reduced);
  scene.background = new THREE.Color('#182c49');
  scene.fog = new THREE.FogExp2('#283f54', .004);
  const camera = new THREE.PerspectiveCamera(46, 1, .15, 500);
  scene.add(new THREE.HemisphereLight('#e3edff', '#497259', 1.65));
  const sun = new THREE.DirectionalLight('#ffe0a9', 2.6);
  sun.position.set(14, 30, 18); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -34, right: 34, top: 34, bottom: -34, near: 1, far: 85 });
  sun.shadow.camera.updateProjectionMatrix();
  sun.shadow.normalBias = .08; sun.shadow.bias = -.00015;
  scene.add(sun);
  const moonlight = new THREE.DirectionalLight('#b6cbff', .9); moonlight.position.set(-18, 13, -20); scene.add(moonlight);

  const materials = new Map();
  function material(color, glow = false) {
    const key = color + glow;
    if (!materials.has(key)) materials.set(key, new THREE.MeshStandardMaterial({ color, roughness: .66, metalness: color === COLORS.gold ? .48 : .04, flatShading: true, ...(glow ? { emissive: color, emissiveIntensity: .65 } : {}) }));
    return materials.get(key);
  }
  const roofShape = new THREE.Shape(); roofShape.moveTo(-.5, -.5); roofShape.lineTo(.5, -.5); roofShape.lineTo(0, .5); roofShape.closePath();
  const geometries = {
    box: new THREE.BoxGeometry(1, 1, 1), cone: new THREE.ConeGeometry(1, 1, 8), cylinder: new THREE.CylinderGeometry(1, 1, 1, 12),
    sphere: new THREE.IcosahedronGeometry(1, 1), roof: new THREE.ExtrudeGeometry(roofShape, { depth: 1, bevelEnabled: false }).translate(0, 0, -.5),
    torus: new THREE.TorusGeometry(1, .11, 6, 24),
  };
  const environment = new THREE.Group(); scene.add(environment);
  function part(parent, geometry, color, x, y, z, sx, sy, sz, glow = false) {
    const mesh = new THREE.Mesh(geometries[geometry], material(color, glow));
    mesh.position.set(x, y, z); mesh.scale.set(sx, sy, sz); mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  }
  const box = (g,x,y,z,w,h,d,c) => part(g,'box',c,x,y,z,w,h,d);
  const ball = (g,x,y,z,r,c) => part(g,'sphere',c,x,y,z,r,r,r);
  const cylinder = (g,x,y,z,r,h,c) => part(g,'cylinder',c,x,y,z,r,h,r);
  const cone = (g,x,y,z,r,h,c) => part(g,'cone',c,x,y,z,r,h,r);
  function group(parent, x = 0, y = 0, z = 0) { const g = new THREE.Group(); g.position.set(x,y,z); parent.add(g); return g; }
  function tree(g, x, z, size = 1, fruit = false) {
    const t = group(g,x,0,z); t.scale.setScalar(size);
    cylinder(t,0,.65,0,.12,1.3,'#73553a');
    if (fruit) {
      ball(t,0,1.65,0,.9,'#548767'); ball(t,-.45,1.4,.1,.55,'#679d72');
      for (let i=0;i<5;i++) ball(t,Math.sin(i*2.3)*.65,1.4+(i%2)*.4,Math.cos(i*2.3)*.65,.12,'#c64b54');
    } else {
      cone(t,0,1.3,0,.75,1.5,'#3f7968'); cone(t,0,1.9,0,.57,1.35,'#558d76'); cone(t,0,2.4,0,.36,1.05,'#7aab86');
    }
  }
  function house(g, { w=1.9, h=1.6, d=1.65, wall='#f2dec3', roof='#98515f' } = {}) {
    box(g,0,h/2,0,w,h,d,wall);
    part(g,'roof',roof,0,h+.48,0,w+.3,1.05,d+.32);
    box(g,0,.48,d/2+.025,.38,.92,.07,'#634236');
    for (const x of [-w*.3,w*.3]) box(g,x,h*.62,d/2+.04,.29,.38,.08,COLORS.light);
    box(g,-w*.3,h+.65,-d*.2,.23,.9,.25,'#c19b83');
    box(g,0,.05,d/2+.22,.6,.12,.42,'#c4ac89');
  }
  function tower(g,x,z,h=4,r=.55,roof=COLORS.roof) {
    cylinder(g,x,h/2,z,r,h,COLORS.stone); cylinder(g,x,h-.2,z,r*1.12,.28,'#bfae9c');
    cone(g,x,h+.65,z,r*1.45,1.65,roof);
    box(g,x,h*.64,z+r,.24,.55,.08,COLORS.light);
    cylinder(g,x,h+1.6,z,.03,.5,COLORS.gold);
    const flag=box(g,x+.2,h+1.72,z,.4,.22,.035,COLORS.gold); flag.rotation.z=.14;
  }
  function palace(g, color=COLORS.roof, scale=1) {
    g.scale.setScalar(scale); house(g,{w:2.6,h:2.7,d:2,wall:'#e5d6c4',roof:color});
    tower(g,-1.4,.25,3.8,.48,color); tower(g,1.4,.25,3.8,.48,color);
    box(g,0,2.4,1.04,.5,.85,.05,COLORS.light);
  }
  function well(g) {
    cylinder(g,0,.35,0,.75,.7,'#bcb6a1'); cylinder(g,0,.72,0,.58,.035,'#334e58');
    for(const x of [-.8,.8])box(g,x,1,0,.13,1.7,.15,COLORS.wood);
    part(g,'roof','#8b4764',0,1.95,0,2,.7,1.4); box(g,0,1.3,0,1.6,.12,.12,COLORS.wood);
  }
  function chest(g) {
    box(g,0,.35,0,1.45,.7,.9,'#aa7148');
    const lid=cylinder(g,0,.75,0,.45,1.45,'#a8673d'); lid.rotation.z=Math.PI/2;
    for(const x of [-.5,.5])box(g,x,.65,0,.12,.85,1,COLORS.gold);
    box(g,0,.6,.51,.23,.25,.08,COLORS.gold);
  }
  function crystal(g) {
    cylinder(g,0,.22,0,.6,.45,COLORS.gold); cylinder(g,0,.72,0,.24,.75,'#725678');
    const glass = new THREE.Mesh(geometries.sphere,new THREE.MeshPhysicalMaterial({color:'#c4a4ff',metalness:.1,roughness:.12,transmission:.22,transparent:true,opacity:.86}));
    glass.position.y=1.52;glass.scale.setScalar(.69);glass.castShadow=true;g.add(glass);
    part(g,'sphere','#f1c6ff',0,1.52,0,.26,.26,.26,true);
    const ring=part(g,'torus',COLORS.gold,0,1,0,.56,.56,.56);ring.rotation.x=Math.PI/2;
  }
  function mushroom(g,x,z,size=1) {
    const t=group(g,x,0,z);t.scale.setScalar(size);
    cylinder(t,0,.4,0,.2,.8,'#f2ddba');
    part(t,'sphere','#ad5a7b',0,.91,0,.7,.38,.7);
    for(const a of [-1,0,1])ball(t,a*.31,1.15,.14,.09,'#fff0d0');
    box(t,0,.24,.21,.14,.33,.04,'#785548');
  }
  function carriage(g) {
    part(g,'sphere','#d5b1cb',0,.85,0,1.05,.77,.8);
    cylinder(g,0,1.7,0,.12,.45,COLORS.gold);
    for(const x of [-.85,.85])for(const z of [-.65,.65]) {
      const wheel=part(g,'torus',COLORS.gold,x,.35,z,.35,.35,.35);wheel.rotation.y=Math.PI/2;
    }
    box(g,0,.9,.74,.52,.65,.07,'#5c537a');
  }
  function ship(g) {
    part(g,'sphere','#438a9d',0,.03,0,1.7,.08,1.1);
    part(g,'sphere','#9b6746',0,.45,0,1.4,.4,.56);
    cylinder(g,0,1.7,0,.055,2.8,COLORS.wood);
    box(g,.5,1.9,0,.95,1.35,.05,COLORS.cream);
    box(g,-.35,2.45,0,.6,.7,.05,'#ead9b8');
    box(g,.26,3.12,0,.5,.27,.04,'#634c68');
  }
  function spinningWheel(g) {
    const ring=part(g,'torus',COLORS.gold,0,1,0,.65,.65,.65);
    for(let i=0;i<8;i++) {
      const spoke=box(g,0,1,0,.045,1.2,.06,COLORS.gold);spoke.rotation.z=i*Math.PI/4;
    }
    box(g,0,.25,0,1.35,.18,.8,COLORS.wood);cylinder(g,0,.7,0,.09,1.1,COLORS.wood);
  }
  function lamp(g) {
    part(g,'sphere',COLORS.gold,0,.55,0,.85,.4,.45);
    cylinder(g,0,.16,0,.44,.3,COLORS.gold);
    cone(g,0,1.03,0,.3,.4,COLORS.gold);
    const spout=cone(g,.95,.76,0,.24,1.2,COLORS.gold);spout.rotation.z=-Math.PI/3;
    const handle=part(g,'torus',COLORS.gold,-.7,.65,0,.38,.38,.38);
    part(g,'sphere','#bfc7fc',1.42,1.5,0,.35,.6,.35,true);
  }
  function makeModel(index,g) {
    if ([2,17,33].includes(index))return chest(g);
    if ([7,22,36].includes(index))return crystal(g);
    if(index===0) { tower(g,-1,0,2.5,.4);tower(g,1,0,2.5,.4);box(g,0,1.7,0,1.8,.3,.45,COLORS.gold);return; }
    if(index===10) { tower(g,0,0,6.3,.8);box(g,.35,2,.86,.14,3.7,.1,'#e5b75e');tree(g,-1.4,-.6,.7);return; }
    if(index===20) { box(g,0,.35,0,2,.6,1.4,'#9c6388');box(g,0,.7,-.4,1.6,.25,.4,COLORS.cream);tree(g,-1.2,-.8,.7);return; }
    if(index===30) { tree(g,-.8,0,1);tree(g,.8,0,.9);const arch=part(g,'torus','#597d66',0,1.3,0,1,1.4,1);return; }
    if(index===4)return spinningWheel(g);
    if(index===12)return lamp(g);
    if(index===28)return well(g);
    if(index===8)return carriage(g);
    if(index===34)return ship(g);
    if(index===31) { mushroom(g,0,0,1.3);mushroom(g,.9,-.5,.7);tree(g,-.9,-.4,.75);return; }
    if(index===32) { part(g,'sphere','#559ca7',0,.08,0,1.5,.1,1.1);ball(g,-.8,.4,0,.5,'#c7baa1');tree(g,.85,-.5,.7);return; }
    if(index===35) { tree(g,0,0,1.2,true);tree(g,1,-.5,.8,true);return; }
    if(index===1) { tree(g,0,0,1.3);tree(g,-.9,-.4,.8);tree(g,.9,-.4,.9);return; }
    if(index===23) { for(let j=0;j<7;j++){const x=Math.sin(j*2.4),z=Math.cos(j*2.4)*.6;cylinder(g,x,.28,z,.025,.5,'#548267');ball(g,x,.56,z,.16,j%2?'#e8c868':'#d191ab');}tree(g,0,-.7,.8);return; }
    if(index===37) { part(g,'sphere','#a6a294',0,.85,0,1.4,1.2,.9);part(g,'sphere','#3e454a',0,.62,.75,.5,.66,.1);for(let j=0;j<4;j++)ball(g,(j-1.5)*.3,.15,1,.13,COLORS.gold);return; }
    if(index===38) { cylinder(g,0,.2,0,.65,.4,'#b3b1ad');ball(g,0,1.1,0,.6,'#80909b');cone(g,-.33,1.75,0,.18,.6,'#80909b');cone(g,.33,1.75,0,.18,.6,'#80909b');part(g,'sphere','#46515e',0,1,.57,.25,.22,.4);return; }
    if([9,14,29,39].includes(index)) {
      palace(g,index===9?'#6c91b5':index===14?'#b66f88':COLORS.roof,.85);
      if(index===39){const frame=part(g,'torus',COLORS.gold,0,1.8,1.16,.6,.9,.6);part(g,'sphere','#7aa4b8',0,1.8,1.15,.53,.8,.06);}
      return;
    }
    if([11,13].includes(index)) { tower(g,0,0,index===13?4.6:3.6,.7,'#71506f');tree(g,1,-.3,.65);return; }
    const roofs={3:'#aa5962',5:'#7f625d',6:'#7c98af',15:'#a2b7c3',16:'#c8ad66',18:'#966d48',19:'#b76152',21:'#a24d5d',24:'#a15a59',25:'#8370a3',26:'#c19858',27:'#8c6a52'};
    house(g,{roof:roofs[index]||COLORS.roof,wall:index===19?'#c18869':index===18?'#b89c73':'#efdec4',h:index===27?2.4:1.6});
    if(index===3) { for(const x of [-.7,0,.7])ball(g,x,2.25,.65,.13,'#f1c4ca'); }
    if(index===15){const glass=part(g,'sphere','#b4d9e5',0,2.35,0,.28,.4,.6);}
    if(index===25)box(g,0,1,1.5,1.7,.06,.9,'#ad6387');
  }

  // A wooden board with raised parchment spaces and printed labels.
  box(environment,0,-.7,0,47,1.3,47,COLORS.edge);
  box(environment,0,-.12,0,46,.2,46,COLORS.wood);
  box(environment,0,.03,0,32.5,.22,32.5,COLORS.grass);
  for(const side of [-1,1]) {
    box(environment,side*22.5,.16,0,.18,.22,45.2,COLORS.gold);
    box(environment,0,.16,side*22.5,45.2,.22,.18,COLORS.gold);
  }
  function labelTexture(square) {
    const c=document.createElement('canvas');c.width=512;c.height=384;const ctx=c.getContext('2d');
    ctx.fillStyle='#f7ead0';ctx.fillRect(0,0,512,384);
    ctx.strokeStyle='#dcc6a2';ctx.lineWidth=3;ctx.strokeRect(10,10,492,364);
    ctx.fillStyle='#433141';ctx.textAlign='center';ctx.textBaseline='middle';ctx.font='bold 49px Planewalker';
    const words=square.name.split(' '),lines=[];let line='';
    for(const word of words){const next=line?line+' '+word:word;if(ctx.measureText(next).width>440&&line){lines.push(line);line=word;}else line=next;}if(line)lines.push(line);
    const start=148-(lines.length-1)*29;
    lines.forEach((text,i)=>ctx.fillText(text,256,start+i*58,455));
    if(square.price||square.amount){ctx.font='36px Planewalker';ctx.fillStyle='#805e35';ctx.fillText((square.price||square.amount)+' GOLD',256,310);}
    const tex=new THREE.CanvasTexture(c);tex.colorSpace=THREE.SRGBColorSpace;tex.anisotropy=Math.min(4,renderer.capabilities.getMaxAnisotropy());return tex;
  }
  const ownership=new Map();
  const frameShape = new THREE.Shape();frameShape.moveTo(-.5,-.5);frameShape.lineTo(.5,-.5);frameShape.lineTo(.5,.5);frameShape.lineTo(-.5,.5);frameShape.closePath();
  const hole=new THREE.Path();hole.moveTo(-.46,-.46);hole.lineTo(-.46,.46);hole.lineTo(.46,.46);hole.lineTo(.46,-.46);hole.closePath();frameShape.holes.push(hole);
  const ownerGeometry=new THREE.ShapeGeometry(frameShape);
  for(const sq of boardData.squares) {
    const c=cell(sq.id);
    box(environment,c.x,.16,c.z,c.w-.1,.3,c.d-.1,'#e8d3ad');
    const sideways=sq.id%10!==0&&c.nx!==0;
    const floor=new THREE.Mesh(new THREE.PlaneGeometry((sideways?c.d:c.w)-.16,(sideways?c.w:c.d)-.16),new THREE.MeshStandardMaterial({map:labelTexture(sq),roughness:.9}));
    floor.rotation.x=-Math.PI/2;floor.rotation.z=sq.id%10===0?(c.nz<0?Math.PI:0):Math.atan2(c.nx,c.nz);floor.position.set(c.x,.319,c.z);floor.receiveShadow=true;environment.add(floor);
    const color=sq.group&&boardData.groups[sq.group].color;
    if(color) {
      const vertical=Math.abs(c.nx)===1;
      box(environment,c.x-c.nx*(c.w/2-.3),.34,c.z-c.nz*(c.d/2-.3),vertical?.48:c.w-.12,.05,vertical?c.d-.12:.48,color);
    }
    const marker=new THREE.Mesh(ownerGeometry,new THREE.MeshStandardMaterial({color:'#d5ae56',emissive:'#d5ae56',emissiveIntensity:.2}));
    marker.rotation.x=-Math.PI/2;marker.scale.set(c.w-.12,c.d-.12,1);marker.position.set(c.x,.36,c.z);marker.visible=false;scene.add(marker);ownership.set(sq.id,marker);
    const inward=c.nx!==0&&c.nz!==0?6.9:Math.max(Math.abs(c.nx)*c.w,Math.abs(c.nz)*c.d)/2+1.75;
    const model=group(environment,c.x-c.nx*inward,.17,c.z-c.nz*inward);
    model.rotation.y=Math.atan2(c.nx,c.nz);makeModel(sq.id,model);
  }

  // A living-looking miniature kingdom inside the path, in the homepage palette.
  const castle=group(environment,1,.18,-2);palace(castle,COLORS.roof,1.75);
  tower(castle,0,-1.5,5.8,.6,'#6a4773');
  box(environment,1,.18,5,2,.05,10,'#d2bd93');
  const pond=part(environment,'sphere','#5ca0ab',-5,.2,3,3.7,.12,2.5);
  const bridge=group(environment,-4,.32,3);box(bridge,0,.32,0,1.5,.15,3.8,COLORS.wood);
  for(const x of [-.75,.75]){box(bridge,x,.73,0,.09,.12,3.8,COLORS.wood);for(const z of [-1.6,0,1.6])box(bridge,x,.5,z,.12,.8,.12,COLORS.wood);}
  for(let i=0;i<34;i++) {
    const x=Math.sin(i*2.399)*((i%6)*1.45+3),z=Math.cos(i*2.399)*((i%5)*1.7+3);
    if(Math.hypot(x-1,z+2)<5 || Math.hypot(x+5,z-3)<3)continue;
    tree(environment,x,z,.55+(i%4)*.15,i%6===0);
  }
  for(const [x,z] of [[7,4],[-7,-5],[8,-7]]){const village=group(environment,x,.17,z);house(village,{w:1.5,h:1.2,d:1.4,roof:'#ae7484'});}
  // The distant horizon belongs to the close-up; keep it outside the overview.
  const horizon = group(scene);horizon.visible=false;
  for(let i=0;i<12;i++){const a=i*Math.PI/6;part(horizon,'sphere',i%2?'#376b68':'#476f78',Math.sin(a)*46,-3,Math.cos(a)*46,8+(i%3),6+(i%4),9);}

  // Instance repeated static geometry: detailed scenery without hundreds of draw calls.
  environment.updateWorldMatrix(true,true);
  const batches=new Map(),originals=[];
  environment.traverse(node=>{if(!node.isMesh||!Object.values(geometries).includes(node.geometry))return;const key=node.geometry.uuid+node.material.uuid;if(!batches.has(key))batches.set(key,[]);batches.get(key).push(node);originals.push(node);});
  for(const meshes of batches.values()){
    const mesh=new THREE.InstancedMesh(meshes[0].geometry,meshes[0].material,meshes.length);
    meshes.forEach((item,i)=>mesh.setMatrixAt(i,item.matrixWorld));mesh.castShadow=true;mesh.receiveShadow=true;scene.add(mesh);
  }
  originals.forEach(mesh=>mesh.removeFromParent());

  const pieces=new Map();let state=null,visible=false,raf=null,lastTime=0,initialized=false;
  let yaw=0,zoom=1,drag=null,mode="full";
  const aim=new THREE.Vector3(),desiredAim=new THREE.Vector3(),desiredCamera=new THREE.Vector3();
  // Track the ground beneath the piece. Its jump is animation, not camera motion.
  const followGround=new THREE.Vector3(),groundTarget=new THREE.Vector3();
  let groundReady=false;
  function tokenModel(player) {
    const g=new THREE.Group();scene.add(g);
    cylinder(g,0,.18,0,.48,.22,COLORS.gold);
    cylinder(g,0,.32,0,.42,.13,PLAYER[player.id]||'#8d638f');
    if(player.lane===1){
      cylinder(g,0,.48,0,.58,.14,'#704777');cone(g,0,1.2,0,.42,1.45,'#855b9c');
      cylinder(g,0,.7,0,.36,.17,COLORS.gold);ball(g,.15,1.17,.25,.07,COLORS.gold);
    }else if(player.lane===2){
      box(g,0,.77,0,.64,.7,.5,COLORS.stone);for(const x of [-.3,.3]){cylinder(g,x,.88,0,.18,.9,COLORS.stone);cone(g,x,1.42,0,.23,.45,PLAYER[player.id]||'#397f94');}
      box(g,0,.63,.26,.16,.3,.03,'#785b53');
    }else{
      cylinder(g,0,.61,0,.4,.42,COLORS.gold);
      for(let i=0;i<5;i++){const a=i*Math.PI*2/5;cone(g,Math.sin(a)*.31,1.05,Math.cos(a)*.31,.12,.64,COLORS.gold);ball(g,Math.sin(a)*.32,.64,Math.cos(a)*.32,.075,PLAYER[player.id]||'#9e638b');}
    }
    const badgeCanvas=document.createElement('canvas');badgeCanvas.width=128;badgeCanvas.height=128;const ctx=badgeCanvas.getContext('2d');
    ctx.fillStyle=PLAYER[player.id]||'#875a82';ctx.beginPath();ctx.arc(64,64,58,0,Math.PI*2);ctx.fill();ctx.lineWidth=7;ctx.strokeStyle='#f6e7c8';ctx.stroke();ctx.font='bold 65px Planewalker';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#fff1dc';ctx.fillText(tokenGlyph(player),64,68);
    const texture=new THREE.CanvasTexture(badgeCanvas);texture.colorSpace=THREE.SRGBColorSpace;
    const badge=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,depthTest:false}));badge.position.y=2.2;badge.scale.set(.7,.7,.7);g.add(badge);
    return {group:g,position:new THREE.Vector3(),cameraGround:new THREE.Vector3(),lane:player.lane,badge};
  }
  canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!drag)return;yaw+=(e.clientX-drag.x)*.006;drag.x=e.clientX;});
  canvas.addEventListener('pointerup',()=>{drag=null;});canvas.addEventListener('pointercancel',()=>{drag=null;});
  canvas.addEventListener('wheel',e=>{if(!e.ctrlKey || mode==='full')return;e.preventDefault();zoom=THREE.MathUtils.clamp(zoom+e.deltaY*.001,.72,1.35);},{passive:false});
  canvas.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){yaw+=e.key==='ArrowLeft'?-.15:.15;e.preventDefault();}else if(e.key==='Home'){yaw=0;zoom=1;e.preventDefault();}});
  let renderWidth=0,renderHeight=0;
  const resize=()=>{
    const w=viewport.clientWidth,h=viewport.clientHeight;
    if(!w||!h||(w===renderWidth&&h===renderHeight))return;
    renderWidth=w;renderHeight=h;
    renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();
    // Changing canvas dimensions clears its buffer. Repaint before the next paint.
    if(visible&&initialized&&!document.hidden){if(raf!==null)cancelAnimationFrame(raf);frame(performance.now());}
  };
  new ResizeObserver(resize).observe(viewport);
  function updateState(next) {
    state=next;
    for(const player of state.players){if(!pieces.has(player.id))pieces.set(player.id,tokenModel(player));pieces.get(player.id).group.visible=!player.bankrupt;}
    for(const [index,marker] of ownership){const owner=state.owners?.[String(index)];marker.visible=!!owner;if(owner){marker.material.color.set(PLAYER[owner]||COLORS.gold);marker.material.emissive.set(PLAYER[owner]||COLORS.gold);}}
  }
  let focusId=null,queuedRoll=null,waitingDicePlayer=null,dicePlayer=null;
  function frame(now) {
    raf=null;if(!visible||document.hidden)return;
    const dt=Math.min((now-lastTime)/1000||.016,.05);lastTime=now;
    const board=viewport.querySelector('#board');
    const width=board?.clientWidth||1,height=board?.clientHeight||1;
    for(const token of board?.querySelectorAll('.mm-token')||[]){
      const p=pieces.get(token.dataset.player);if(!p)continue;
      const style=getComputedStyle(token);
      const layoutWidth=Number(token.dataset.layoutWidth)||width,layoutHeight=Number(token.dataset.layoutHeight)||height;
      const x=((parseFloat(style.left)+parseFloat(style.width)/2)/layoutWidth-.5)*SIZE;
      const z=((parseFloat(style.top)+parseFloat(style.height)/2)/layoutHeight-.5)*SIZE;
      if(!Number.isFinite(x)||!Number.isFinite(z))continue;
      // The shared renderer stamps each step: one arc, then a grounded landing.
      const elapsed = now - Number(token.dataset.hopStart || 0);
      const duration = Number(token.dataset.hopDuration || 0);
      const progress = duration > 0 ? Math.min(1, Math.max(0, elapsed / duration)) : 1;
      const hop = !reduced && progress < 1 ? Math.sin(Math.PI * progress) ** 2 * .46 : 0;
      p.group.position.set(x, .34 + hop, z);
      p.position.copy(p.group.position);
      const square=cell(Number(token.dataset.square));
      p.cameraGround.set(x + (.5-(.23+.27*(p.lane-1)))*square.w,.34,z-.24*square.d);
    }
    const focus=pieces.get(focusId);
    // Dice share the player's view; rolling never creates a cutaway or a return pan.
    const toss=diceThrow.update(now);
    if(mode==='full') {
      const pose=overviewPose(camera.aspect,yaw);
      desiredCamera.copy(pose.position);desiredAim.copy(pose.target);
    } else if(focus){
      groundTarget.copy(focus.cameraGround);
      if(!groundReady||!initialized||reduced){followGround.copy(groundTarget);groundReady=true;}
      else dampAroundBoard(followGround,groundTarget,1-Math.exp(-dt*3.6),dt*1.8);
      const p=followGround;const radial=new THREE.Vector2(p.x,p.z).normalize();const angle=Math.atan2(radial.x,radial.y)+yaw;
      const nx=Math.sin(angle),nz=Math.cos(angle),tx=nz,tz=-nx;
      const framing=Math.max(1,1/camera.aspect);
      desiredCamera.set(p.x+nx*9*zoom*framing+tx*1.2,.34+6.3*zoom*framing,p.z+nz*9*zoom*framing+tz*1.2);
      desiredAim.set(p.x,.34+1.1,p.z);
    }
    if(mode==='full'||focus){
      // Hold the current framing even if Roll is clicked during a turn handoff.
      if(!toss?.active||!initialized){
        if(mode==='full'||!initialized||reduced){camera.position.copy(desiredCamera);aim.copy(desiredAim);initialized=true;}
        else{dampAroundBoard(camera.position,desiredCamera,1-Math.exp(-dt*4.8),dt*1.8);dampAroundBoard(aim,desiredAim,1-Math.exp(-dt*7),dt*1.8);}
      }
      camera.lookAt(aim);
      if(focus)focus.badge.material.opacity=1;
    }
    diceThrow.avoidTokens(camera, [...pieces.values()].filter(p => p.group.visible).map(p => p.position));
    renderer.render(scene,camera);
    if(queuedRoll){const pending=queuedRoll;queuedRoll=null;rollDice(pending.event,pending.receivedAt);}
    raf=requestAnimationFrame(frame);
  }
  function setVisible(value){
    if(value===visible)return;
    visible=value;
    if(value){
      resize();
      if(raf===null&&!document.hidden){lastTime=performance.now();frame(lastTime);}
      // Reveal only after a complete frame, including first load and 3D re-entry.
      canvas.hidden=false;
    }else{
      canvas.hidden=true;
      if(raf!==null){cancelAnimationFrame(raf);raf=null;}
    }
  }
  document.addEventListener('visibilitychange',()=>{if(visible&&!document.hidden&&raf===null){lastTime=performance.now();raf=requestAnimationFrame(frame);}});
  function queueDice(event,receivedAt){
    queuedRoll={event,receivedAt};
    if(waitingDicePlayer!==event.player){
      waitingDicePlayer=event.player;
      viewport.querySelector('#board')?.dispatchEvent(new CustomEvent('board-dice-wait',{detail:{player:event.player}}));
    }
  }
  function rollDice(event,receivedAt=performance.now()) {
    // Let an existing turn handoff finish before exposing dice on its square.
    if(!initialized){queueDice(event,receivedAt);return;}
    const player=state?.players.find(p=>p.id===event.player);
    if(!player)return;
    const from=Number.isInteger(event.from)?event.from:state.pending?.kind==='move'&&state.pending.player===player.id?state.pending.from:player.position;
    // Only choose positions within the active player's starting square.
    // Smaller dice leave room for the token; no adjacent/distant tile search.
    const square = cell(from);
    const visiblePieces = [...pieces.values()].filter(p => p.group.visible).map(p => p.position);
    camera.updateMatrixWorld();
    let origin = { ...square }, best = -Infinity;
    const spots=[];
    for(const x of [-1,0,1])for(const z of [-1,0,1])spots.push({x:x*(square.w/2-.96),z:z*(square.d/2-.96)});
    const layouts=[];
    for(let a=0;a<spots.length;a++)for(let b=a+1;b<spots.length;b++){
      if(Math.hypot(spots[a].x-spots[b].x,spots[a].z-spots[b].z)>=1.6)layouts.push({...square,spots:[spots[a],spots[b]]});
    }
    for (const candidate of layouts) {
      let clearance = Infinity;
      for (let i = 0; i < 2; i++) for (let step = 0; step <= 10; step++) {
        const point = dicePath(candidate, i, step / 10);
        for (const token of visiblePieces) clearance = Math.min(clearance, Math.hypot(point.x - token.x, point.z - token.z));
      }
      const screen = diceThrow.landingClearance(camera, candidate, visiblePieces);
      const score = (screen >= .01 ? 10000 : 0) + (clearance >= 1.3 ? 1000 : 0) + Math.min(clearance, 3) * 10 + Math.min(screen, .3);
      if (score > best) { best = score; origin = candidate; }
    }
    if(best<10000&&mode==='follow'&&performance.now()-receivedAt<4500){queueDice(event,receivedAt);return;}
    waitingDicePlayer=null;dicePlayer=event.player;
    viewport.querySelector('#board')?.dispatchEvent(new CustomEvent('board-dice-start', { detail: { player: event.player } }));
    diceThrow.start(event.dice,origin);
  }
  return { updateState, setVisible, clearDice() { const player=waitingDicePlayer||dicePlayer;queuedRoll=null;waitingDicePlayer=null;dicePlayer=null;diceThrow.clear();if(player)viewport.querySelector('#board')?.dispatchEvent(new CustomEvent('board-dice-cancel',{detail:{player}})); }, rollDice, setMode(value){
    if(mode===value)return;
    mode=value;yaw=0;zoom=1;horizon.visible=mode==='follow';
    scene.fog.density=mode==='full'?.004:.012;
    canvas.setAttribute('aria-label',mode==='full'
      ? 'Full 3D storybook board, all 40 spaces. Drag or use arrow keys to rotate. Home resets the camera.'
      : '3D storybook board following the token. Drag or use arrow keys to look around. Home resets the camera.');
  }, focus(id){focusId=id;}, resetView(){yaw=0;zoom=1;initialized=false;}, canvas };
}
