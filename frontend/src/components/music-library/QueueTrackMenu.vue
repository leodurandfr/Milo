<!-- frontend/src/components/music-library/QueueTrackMenu.vue -->
<!-- The ⋯ menu of a row of the live queue: adding its track to a playlist, and
     taking the row out of the queue. Queueing it again is not offered, so the
     menu is not LibraryItemMenu's. -->
<template>
  <Dropdown model-value="" :options="options" size="small" placement="top-end" icon-placement="start"
    @change="choose">
    <template #trigger="{ toggle }">
      <button v-press type="button" class="row-trigger hit-outset" :aria-label="t('musicLibrary.moreOptions')"
        @click="toggle">
        <SvgIcon name="threeDots" :size="24" />
      </button>
    </template>
  </Dropdown>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import Dropdown from '@/components/ui/Dropdown.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  // Whether the row may leave the queue: never the track playing, and only
  // while the source takes the command.
  removable: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['playlist', 'remove']);

const { t } = useI18n();

const options = computed(() => [
  { value: 'playlist', label: t('musicLibrary.playlists.addToPlaylist'), icon: 'plusCircle' },
  ...(props.removable ? [{ value: 'remove', label: t('musicLibrary.removeFromQueue'), icon: 'minus' }] : []),
]);

function choose(action) {
  emit(action);
}
</script>

<style scoped>
.row-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: var(--radius-02);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: color var(--transition-fast), var(--transition-press);
}
</style>
