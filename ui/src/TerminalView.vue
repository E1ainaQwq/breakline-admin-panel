<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount,watch,nextTick} from 'vue';
import {Terminal} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import {api,query} from './api';
const props=defineProps<{server:string,active:boolean}>();
const emit=defineEmits<{command:[value:string]}>();
const host=ref<HTMLElement>();const paused=ref(false);const connected=ref(false);const note=ref('');const follow=ref(true);
let terminal:Terminal;let fit:FitAddon;let resize:ResizeObserver;let timer:ReturnType<typeof setTimeout>|undefined;let cursor='';let generation=0;let busy=false;
function schedule(){clearTimeout(timer);timer=setTimeout(poll,1500);}
async function poll(){
 const g=generation;if(busy||!props.active||paused.value||document.hidden){schedule();return;}busy=true;
 try{const value=await api(query('instance/logs',{server:props.server,cursor}));if(g!==generation)return;
  connected.value=value.available;note.value=value.message||'';if(value.reset)terminal.clear();cursor=value.cursor;
  if(value.text){terminal.write(value.text.replace(/\r?\n/g,'\r\n'));if(follow.value)terminal.scrollToBottom();}
 }catch(e:any){if(g===generation){connected.value=false;note.value=e.message;}}
 finally{busy=false;if(g===generation)schedule();}
}
function restart(){generation++;clearTimeout(timer);cursor='';connected.value=false;if(terminal)terminal.reset();void poll();}
function clear(){terminal.clear();}
onMounted(()=>{terminal=new Terminal({disableStdin:true,convertEol:true,fontSize:14,fontFamily:'Consolas, "Cascadia Mono", monospace',scrollback:10000,theme:{background:'#1e1e1e',foreground:'#d4d4d4'},allowProposedApi:false});fit=new FitAddon();terminal.loadAddon(fit);terminal.open(host.value!);resize=new ResizeObserver(()=>fit.fit());resize.observe(host.value!);fit.fit();void poll();});
watch(()=>props.server,restart);watch(()=>props.active,async v=>{if(v){await nextTick();fit?.fit();} });
watch(paused,v=>{if(!v){clearTimeout(timer);void poll();}});
onBeforeUnmount(()=>{generation++;clearTimeout(timer);resize.disconnect();terminal.dispose();});
defineExpose({clear});
</script>
<template><div class="terminal-top"><a-space wrap><a-badge :status="connected?'success':'default'" :text="paused?'已暂停':connected?'已连接日志':'等待连接'"/><a-button size="small" @click="paused=!paused">{{paused?'继续输出':'暂停输出'}}</a-button><a-button size="small" @click="clear">清屏</a-button><a-checkbox v-model:checked="follow">自动滚动</a-checkbox></a-space><span class="subtle">{{note}}</span></div><div ref="host" class="terminal-canvas" aria-label="服务器实时日志"></div></template>
