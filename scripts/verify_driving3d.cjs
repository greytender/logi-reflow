const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const sandbox={window:{},document:{currentScript:{src:'http://localhost/driving3d.js'}},URL,Math,Number};
vm.runInNewContext(fs.readFileSync('web/driving3d.js','utf8'),sandbox);
const {pathAt,roadPath}=sandbox.window.LogiDriving3D;
const path=[{x:-80,z:44},{x:40,z:44},{x:40,z:0}];
assert.equal(pathAt(path,0).x,-80);assert.equal(pathAt(path,0).z,44);
assert.equal(pathAt(path,1).x,40);assert.equal(pathAt(path,1).z,0);
assert.equal(pathAt(path,-3).x,-80);assert.equal(pathAt(path,8).z,0);
for(let i=0;i<=1000;i++){const p=pathAt(path,i/1000);assert(Number.isFinite(p.x)&&Number.isFinite(p.z));assert(Math.abs(Math.hypot(p.dx,p.dz)-1)<1e-8);assert(Math.abs(p.z-44)<4.5||Math.abs(p.x-40)<4.5);const laneX=p.x-p.dz*1.5,laneZ=p.z+p.dx*1.5;assert(Math.abs(laneZ-44)<4.5||Math.abs(laneX-40)<4.5);}
// Camera right is forward cross world-up: +X heading has right along +Z.
const heading=pathAt(path,0);assert(Math.abs(-heading.dz)<1e-12);assert.equal(heading.dx,1);
const rounded=roadPath(path);assert(rounded.length>3);assert(pathAt(path,.5).length>0);
assert.equal(pathAt([{x:2,z:3}],.5).x,2);
assert.equal(pathAt([],.2).length,0);
(async()=>{const {pathToFileURL}=require('node:url'),pathUtil=require('node:path');const T=await import(pathToFileURL(pathUtil.resolve('web/vendor/three.module.min.js')).href);for(const [dx,dz] of [[1,0],[0,1],[-1,0],[0,-1],[Math.SQRT1_2,Math.SQRT1_2]]){const matrix=new T.Matrix4().lookAt(new T.Vector3(),new T.Vector3(dx,0,dz),new T.Vector3(0,1,0));assert(Math.abs(matrix.elements[0]+dz)<1e-10);assert(Math.abs(matrix.elements[2]-dx)<1e-10);}console.log('PASS road endpoints, clamped seeking, finite headings, road-contained right lane offset, Three.js lookAt camera-right basis, empty and one-point paths');})().catch(e=>{console.error(e);process.exitCode=1;});
