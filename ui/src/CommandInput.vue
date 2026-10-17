<script setup lang="ts">
import {ref,computed,onMounted,watch} from 'vue';
import {api} from './api';
import {complete,type Guide,type Candidate} from './completion';
const props=defineProps<{modelValue:string;server:string;history:string[]}>();const emit=defineEmits<{'update:modelValue':[string];submit:[]}>();
const guides=ref<Guide[]>([]),players=ref<string[]>([]),focused=ref(false),dismissed=ref(false),active=ref(0),field=ref<any>(),failure=ref('');let historyIndex=0,draft='',accepted:string|null=null;
const suggestions=computed(()=>dismissed.value?[]:complete(props.modelValue,guides.value,players.value));
const guide=computed(()=>[...guides.value].sort((a,b)=>b.en.length-a.en.length).find(g=>props.modelValue===g.en||props.modelValue.startsWith(g.en+' ')));
const open=computed(()=>focused.value&&suggestions.value.length>0);
watch(()=>props.modelValue,value=>{active.value=0;if(value!==accepted)dismissed.value=false;});
async function loadPlayers(){try{const v=await api('online-players');players.value=v.players.filter((p:any)=>p.server===props.server||p.serverId===props.server).map((p:any)=>p.name);if(!players.value.length)players.value=v.players.map((p:any)=>p.name);}catch{players.value=[];}}
watch(()=>props.server,loadPlayers);
onMounted(async()=>{try{guides.value=(await api('console-help')).commands;}catch(e:any){failure.value=e.message;}await loadPlayers();});
function choose(row:Candidate){accepted=row.value;dismissed.value=true;emit('update:modelValue',row.value);field.value?.focus();}
function update(value:string){accepted=null;dismissed.value=false;emit('update:modelValue',String(value));}
function key(e:KeyboardEvent){
 if(e.isComposing)return;
 if(e.key==='Escape'){dismissed.value=true;return;}
 if(open.value&&['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();active.value=(active.value+(e.key==='ArrowDown'?1:-1)+suggestions.value.length)%suggestions.value.length;return;}
 if(open.value&&(e.key==='Tab'||e.key==='Enter')){e.preventDefault();e.stopPropagation();choose(suggestions.value[active.value]);dismissed.value=true;return;}
 if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();if(historyIndex===props.history.length)draft=props.modelValue;historyIndex=Math.max(0,Math.min(props.history.length,historyIndex+(e.key==='ArrowUp'?-1:1)));emit('update:modelValue',props.history[historyIndex]??draft);dismissed.value=true;}
 if(e.key==='Enter'){e.preventDefault();emit('submit');}
}
watch(()=>props.history.length,v=>historyIndex=v,{immediate:true});
</script>
<template><div class="command-completion"><a-input ref="field" :value="modelValue" aria-label="服务器指令" role="combobox" aria-autocomplete="list" :aria-expanded="open" aria-controls="command-candidates" :aria-activedescendant="open?'command-option-'+active:undefined" placeholder="输入中文或英文搜索指令…" @update:value="update" @focus="focused=true;loadPlayers()" @blur="focused=false" @keydown="key"/>
<ul v-if="open" id="command-candidates" role="listbox" class="command-candidates"><li v-for="(row,i) in suggestions" :id="'command-option-'+i" :key="row.value" role="option" :aria-selected="active===i" :class="{active:active===i}" @mousedown.prevent="choose(row);dismissed=true"><strong>{{row.label}}</strong><small>{{row.detail}}</small></li></ul>
<div class="command-hint" aria-live="polite"><template v-if="guide"><strong>{{guide.template}}</strong><span>{{guide.description}}</span><span v-if="guide.parameters.length">参数：{{guide.parameters.map(p=>p.name+(p.optional?'（可选）':'')).join(' → ')}}</span></template><span v-else>{{failure||'↑↓ 选择候选 · Tab / Enter 补全 · Esc 收起 · 再按 Enter 预览执行；无候选时 ↑↓ 查看历史'}}</span></div></div></template>
