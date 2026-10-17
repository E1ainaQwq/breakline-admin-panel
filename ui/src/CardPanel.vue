<!-- Adapted from MCSManager CardPanel.vue, commit aab9f7f, Apache-2.0.
     Modified: use a slot-based operator without the upstream design-mode store. -->
<script setup lang="ts">defineProps<{title?:string}>();</script>
<template><div class="card-panel global-card-container-shadow"><div v-if="title" class="card-panel-title"><a-typography-title :level="5" style="margin:0">{{title}}</a-typography-title><div class="card-operator"><slot name="operator"/></div></div><div class="card-panel-content"><slot/></div></div></template>
