export interface Guide {en:string;zh:string;template:string;description:string;parameters:{name:string;type:string;default?:string;optional?:boolean}[]}
export interface Candidate {value:string;label:string;detail:string}
export function complete(input:string,guides:Guide[],players:string[]=[]):Candidate[]{
 const text=input.replace(/^\//,'');const lower=text.toLowerCase();
 const guide=[...guides].sort((a,b)=>b.en.length-a.en.length).find(g=>text.startsWith(g.en+' '));
 if(guide){
  const tail=text.slice(guide.en.length+1),words=tail.split(/\s+/),index=words.length-1,param=guide.parameters[index];
  if(param?.type==='player')return players.filter(p=>p.toLowerCase().startsWith(words[index].toLowerCase())).slice(0,10).map(p=>({value:guide.en+' '+[...words.slice(0,-1),p].join(' ')+' ',label:p,detail:'在线玩家 · '+param.name}));
  if(index===0){
   const literals=guide.description.match(/子项[：:]\s*([^。；]+)/)?.[1].split('/').map(s=>s.trim()).filter(s=>/^[a-z][a-z0-9-]*$/i.test(s))??[];
   if(param?.default)literals.push(param.default);
   return literals.filter(value=>value.toLowerCase().startsWith(tail.toLowerCase())).slice(0,12).map(value=>({value:guide.en+' '+value+' ',label:value,detail:guide.zh+' · '+(param?.name||'子指令')}));
  }
  return [];
 }
 return guides.filter(g=>(g.en+' '+g.zh+' '+g.description).toLowerCase().includes(lower)).sort((a,b)=>Number(b.en.startsWith(lower))-Number(a.en.startsWith(lower))).slice(0,10).map(g=>({value:g.en+(g.parameters.length?' ':''),label:g.zh+' · '+g.en,detail:g.template+' — '+g.description}));
}
