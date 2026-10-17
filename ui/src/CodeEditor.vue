<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount,watch} from 'vue';
import {EditorView,basicSetup} from 'codemirror';
import {EditorState,Compartment} from '@codemirror/state';
import {keymap} from '@codemirror/view';
import {json} from '@codemirror/lang-json';
const props=defineProps<{modelValue:string,path:string,dark:boolean}>();
const emit=defineEmits<{'update:modelValue':[value:string],save:[]}>();
const host=ref<HTMLElement>();const language=new Compartment(),colors=new Compartment();let view:EditorView|undefined;
function palette(){return EditorView.theme({'&':{height:'440px',fontSize:'13px',backgroundColor:props.dark?'#202020':'#fff',color:props.dark?'#d4d4d4':'#262626'},'.cm-scroller':{fontFamily:'Consolas, "Cascadia Mono", monospace',overflow:'auto'},'.cm-content':{padding:'10px 0'},'.cm-gutters':{backgroundColor:props.dark?'#292929':'#fafafa',color:'#8c8c8c',borderRight:'1px solid '+(props.dark?'#424242':'#eee')},'.cm-activeLine':{backgroundColor:props.dark?'#343434':'#f4f8ff'},'.cm-activeLineGutter':{backgroundColor:props.dark?'#343434':'#e6f4ff'},'.cm-cursor':{borderLeftColor:props.dark?'#ddd':'#222'},'&.cm-focused':{outline:'none'}},{dark:props.dark});}
onMounted(()=>{view=new EditorView({parent:host.value,state:EditorState.create({doc:props.modelValue,extensions:[basicSetup,language.of(props.path.endsWith('.json')?json():[]),colors.of(palette()),EditorView.contentAttributes.of({'aria-label':'文件内容','role':'textbox','aria-multiline':'true'}),keymap.of([{key:'Mod-s',run:()=>{emit('save');return true;}}]),EditorView.updateListener.of(update=>{if(update.docChanged)emit('update:modelValue',update.state.doc.toString());})]})});});
watch(()=>props.modelValue,value=>{if(view&&value!==view.state.doc.toString())view.dispatch({changes:{from:0,to:view.state.doc.length,insert:value}});});
watch(()=>props.path,path=>view?.dispatch({effects:language.reconfigure(path.endsWith('.json')?json():[])}));
watch(()=>props.dark,()=>view?.dispatch({effects:colors.reconfigure(palette())}));
onBeforeUnmount(()=>view?.destroy());
</script>
<template><div ref="host" class="code-editor-shell"/><div class="editor-hint">Ctrl / ⌘ + S 保存 · Ctrl / ⌘ + F 搜索</div></template>
<style scoped>.code-editor-shell{border:1px solid var(--color-gray-5);border-radius:6px;overflow:hidden;min-width:0}.editor-hint{font-size:11px;color:#8c8c8c;margin-top:6px}</style>
