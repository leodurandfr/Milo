<!-- LyricsModal.vue — Lyrics app modal (mirrors Equalizer/Multiroom apps).
     Opens over the current source from the dock; fetches lyrics on open and
     refetches when the track changes while open. -->
<template>
  <div class="lyrics-modal">
    <NavigationHeader :title="t('lyrics.title')" />

    <div class="lyrics-body">
      <Transition name="lyrics-fade" mode="out-in">
        <MessageContent v-if="lyricsStore.loading" key="loading"
          :loading="true" :loading-delay="0" :title="t('lyrics.loading')" />

        <MessageContent v-else-if="!lyricsStore.found" key="empty"
          icon="lyrics" :title="emptyTitle" />

        <LyricsContent v-else :key="`content-${activeSource}`" :source="activeSource"
          :synced="lyricsStore.synced" :plain="lyricsStore.plain" />
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore } from '@/stores/lyricsStore';

import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import LyricsContent from './LyricsContent.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();

const activeSource = computed(() => unifiedStore.systemState.active_source);

// Track identity — refetch on change (a new track, or a mid-stream Shazam hit on
// radio). Position updates mutate metadata but not artist/title, so they don't
// retrigger. immediate:true does the initial fetch on open (the modal is only
// mounted while open, like EqualizerModal's onMounted fetch).
const trackKey = computed(() => {
  const m = unifiedStore.systemState.metadata || {};
  return `${m.artist || ''}|||${m.title || ''}`;
});
watch(trackKey, () => lyricsStore.loadLyrics(), { immediate: true });

const emptyTitle = computed(() => {
  const m = unifiedStore.systemState.metadata || {};
  return m.artist && m.title ? t('lyrics.noLyrics') : t('lyrics.notPlaying');
});
</script>

<style scoped>
.lyrics-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.lyrics-body {
  display: flex;
  flex-direction: column;
}

/* Keyed state cross-fade, aligned with the modal's other app bodies. */
.lyrics-fade-leave-active {
  transition: opacity var(--transition-fast-leave);
}

.lyrics-fade-enter-active {
  transition: opacity var(--transition-in-out);
  transition-delay: 100ms;
}

.lyrics-fade-enter-from,
.lyrics-fade-leave-to {
  opacity: 0;
}
</style>
