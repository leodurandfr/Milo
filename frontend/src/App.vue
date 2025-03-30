<template>
  <div class="app-container">
    <router-view />
    <WebSocketStatus />
  </div>
</template>

<script setup>
import WebSocketStatus from './components/WebSocketStatus.vue';
</script>

<style>
.app-container {
  width: 100%;
  height: 100vh;
  background-color: #f5f5f5;
  display: flex;
  flex-direction: column;
}
</style>