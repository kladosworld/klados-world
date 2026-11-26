"use strict";
const {test}=require('node:test'), assert=require('node:assert/strict');
const fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync('arena/static/institutions.js','utf8');
function node(tag){return {tag,children:[],value:'',dataset:{},appendChild(n){this.children.push(n);},set textContent(v){this.value=String(v);this.children=[];},get textContent(){return this.value+this.children.map(n=>n.textContent).join(' ');}};}
async function render(rows,kind='organizations'){
 const root=node('section'), status=node('p'),calls=[];root.dataset.kind=kind;
 vm.runInNewContext(source,{document:{getElementById:id=>id==='institution-results'?root:status,createElement:node},location:{search:''},URLSearchParams,
 fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({[kind]:rows})};}});
 await new Promise(setImmediate);return {root,status,calls};
}
test('empty institutional views remain useful without active founders',async()=>{
 const b=await render([]);assert.match(b.root.textContent,/No public organizations/);assert.equal(b.calls.length,1);assert.equal(b.calls[0].options.credentials,'omit');assert.equal(b.calls[0].options.method,undefined);
});
test('hostile institutional text is rendered as text with internal identity links',async()=>{
 const text='<img src=x onerror=alert(1)>';const b=await render([{id:1,name:text,charter_text:text,created_by:'../private?token=x'}]);
 assert.match(b.root.textContent,/<img/);const nodes=n=>[n,...n.children.flatMap(nodes)];assert.ok(nodes(b.root).every(n=>n.tag!=='img'));
 assert.equal(nodes(b.root).find(n=>n.tag==='a').href,'/society/agents/..%2Fprivate%3Ftoken%3Dx');assert.equal(b.status.textContent,'Read-only public view.');
});
