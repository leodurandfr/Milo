<!-- frontend/src/components/navigation/BottomNavigation.vue -->
<template>
    <nav class="bottom-navigation">
        <router-link to="/" class="nav-link" :class="{ active: $route.name === 'home' }">
            Home
        </router-link>
        <router-link to="/multiroom" class="nav-link" :class="{ active: $route.name === 'multiroom' }">
            Multiroom
        </router-link>
        <button @click="changeSource('librespot')" :disabled="unifiedStore.isTransitioning"
            :class="['nav-link', 'source-nav', { active: unifiedStore.currentSource === 'librespot' }]">
            Spotify
        </button>
        <button @click="changeSource('bluetooth')" :disabled="unifiedStore.isTransitioning"
            :class="['nav-link', 'source-nav', { active: unifiedStore.currentSource === 'bluetooth' }]">
            Bluetooth
        </button>
        <button @click="changeSource('roc')" :disabled="unifiedStore.isTransitioning"
            :class="['nav-link', 'source-nav', { active: unifiedStore.currentSource === 'roc' }]">
            ROC for Mac
        </button>
    </nav>
</template>

<script setup>
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const unifiedStore = useUnifiedAudioStore();

function changeSource(source) {
    unifiedStore.changeSource(source);
}
</script>

<style scoped>
.bottom-navigation {
    width: 460px;
    display: flex;
    background: white;
    border: 1px solid #ddd;
    position: fixed;
    bottom: 0;
}

.nav-link {
    flex: 1;
    padding: 12px 8px;
    text-decoration: none;
    color: #666;
    text-align: center;
    border: none;
    background: none;
    cursor: pointer;
    font-size: 12px;
    font-weight: 500;
}

.nav-link:hover {
    background: #f0f0f0;
}

.nav-link.active {
    background: #2196F3;
    color: white;
}

.nav-link:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.source-nav:disabled {
    background: #f5f5f5;
    color: #999;
}
</style>