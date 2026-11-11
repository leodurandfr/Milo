<!-- frontend/src/components/music-library/LibraryItemMenu.vue -->
<!-- The ⋯ menu of a track row or of a page header in the library: queueing
     what it names after the current track or at the end, adding it to a
     playlist, and on a page header playing the page shuffled. The queue
     entries are offered only while the source takes them, so with nothing
     playing a row's menu holds the playlist alone. -->
<template>
  <Dropdown v-if="options.length" model-value="" :options="options" size="small"
    :placement="trigger === 'row' ? 'top-end' : 'bottom-start'" icon-placement="start" @change="choose">
    <template #trigger="{ toggle }">
      <button v-if="trigger === 'row'" v-press type="button" class="row-trigger hit-outset"
        :aria-label="t('musicLibrary.moreOptions')" :aria-busy="resolving" @click="toggle">
        <SvgIcon name="threeDots" :size="24" />
      </button>
      <IconButton v-else icon="threeDots" variant="on-contrast" size="medium" :loading="resolving"
        :aria-label="t('musicLibrary.moreOptions')" @click="toggle" />
    </template>
  </Dropdown>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import Dropdown from '@/components/ui/Dropdown.vue';
import IconButton from '@/components/ui/IconButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  // The tracks the menu acts on, in play order: an array, or a promise of one
  // for a page whose tracks are fetched on demand (an artist's albums).
  songs: {
    type: Function,
    required: true,
  },
  trigger: {
    type: String,
    default: 'row',
    validator: (value) => ['row', 'header'].includes(value),
  },
  addToPlaylist: {
    type: Boolean,
    default: false,
  },
  // A page header: playing the page shuffled is asked of the page, which owns
  // the play button's spinner and its context.
  shuffle: {
    type: Boolean,
    default: false,
  },
  // A playlist's header: its edit mode is entered from here.
  edit: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['edit', 'shuffle']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const resolving = ref(false);
let alive = true;
onBeforeUnmount(() => { alive = false; });

const options = computed(() => [
  ...(props.shuffle ? [{ value: 'shuffle', label: t('musicLibrary.shuffle'), icon: 'shuffle' }] : []),
  ...(store.canQueue ? [
    { value: 'next', label: t('musicLibrary.playNext'), icon: 'next' },
    { value: 'end', label: t('musicLibrary.addToQueue'), icon: 'queue' },
  ] : []),
  ...(props.addToPlaylist
    ? [{ value: 'playlist', label: t('musicLibrary.playlists.addToPlaylist'), icon: 'plusCircle' }]
    : []),
  ...(props.edit ? [{ value: 'edit', label: t('musicLibrary.playlists.edit'), icon: 'dragHandle' }] : []),
]);

async function choose(action) {
  if (action === 'edit' || action === 'shuffle') {
    emit(action);
    return;
  }
  if (resolving.value) return;
  resolving.value = true;
  let songs;
  try {
    songs = await props.songs();
  } finally {
    resolving.value = false;
  }
  if (!alive || !songs?.length) return;
  if (action === 'playlist') store.requestAddToPlaylist(songs.map((song) => song.id));
  else store.addToQueue(songs, action);
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
