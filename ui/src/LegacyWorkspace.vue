<script setup lang="ts">
import {onMounted,watch,nextTick,ref} from 'vue';
import html from './legacy.html?raw';
import './legacy.css';
const props=defineProps<{page:string,active:boolean,session:any}>();const emit=defineEmits<{navigate:[value:string]}>();
const ready=ref(false);let initializing=true;
const bridge=()=> (window as any).breaklineLegacy;
async function show(){if(!ready.value)return;if(props.active)await bridge().page(props.page);else bridge().pause();}
onMounted(async()=>{await nextTick();await import(/* @vite-ignore */ String('/panel.js'));await bridge().enter(props.session);ready.value=true;await show();initializing=false;window.addEventListener('breakline:navigate',((event:CustomEvent)=>{if(!initializing&&props.active)emit('navigate',event.detail);}) as EventListener);});
watch(()=>[props.page,props.active],show);
</script>
<template><div id="legacy-host" v-html="html"/></template>
