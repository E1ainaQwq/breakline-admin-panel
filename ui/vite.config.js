import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
function thirdPartyLicenses(){return {name:'runtime-license-notices',generateBundle(_,bundle){
 const packages=new Set();
 for(const item of Object.values(bundle)){if(item.type!=='chunk')continue;for(const id of Object.keys(item.modules)){
  const marker='/node_modules/';const position=id.lastIndexOf(marker);if(position<0)continue;const parts=id.slice(position+marker.length).split('/');const name=parts[0].startsWith('@')?parts.slice(0,2).join('/'):parts[0];packages.add(id.slice(0,position+marker.length)+name);
 }}
 const texts=[];for(const dir of [...packages].sort()){const meta=dir+'/package.json';if(!fs.existsSync(meta))continue;const pkg=JSON.parse(fs.readFileSync(meta,'utf8'));const candidates=fs.readdirSync(dir).filter(n=>/^licen[sc]e(?:[.-].*)?$/i.test(n));const licenses=candidates.filter(n=>fs.statSync(dir+'/'+n).isFile()).map(n=>fs.readFileSync(dir+'/'+n,'utf8'));texts.push(pkg.name+' '+pkg.version+' ('+(typeof pkg.license==='string'?pkg.license:'see package notice')+')\n'+licenses.join('\n'));}
 this.emitFile({type:'asset',fileName:'THIRD-PARTY-LICENSES.txt',source:texts.join('\n\n========================================\n\n')});
}};}
export default defineConfig({root:fs.realpathSync(fileURLToPath(new URL('.',import.meta.url))),plugins:[vue(),thirdPartyLicenses()],base:'/mcsm/',build:{outDir:'../web/mcsm',emptyOutDir:true,chunkSizeWarningLimit:1600,rollupOptions:{output:{manualChunks(id){if(id.includes('/node_modules/vue/')||id.includes('/node_modules/@vue/'))return 'vue';if(id.includes('/node_modules/ant-design-vue/')||id.includes('/node_modules/@ant-design/'))return 'ant-design';if(id.includes('/node_modules/@xterm/'))return 'terminal';if(id.includes('/node_modules/@codemirror/')||id.includes('/node_modules/@lezer/')||id.includes('/node_modules/codemirror/'))return 'editor';}}}}});
