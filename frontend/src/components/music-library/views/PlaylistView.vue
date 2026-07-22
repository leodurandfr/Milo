<template>
  <div class="playlist-view">
    <div class="transition-container">
      <Transition name="content-swap">
        <MessageContent v-if="loading && !playlist" key="loading" loading :title="t('musicLibrary.loading')" />
        <MessageContent v-else-if="!playlist" key="notfound" :title="t('musicLibrary.notFound')" />

        <div v-else key="loaded" class="content-stack">
          <TracklistHeader
            :cover-id="playlist.coverArt"
            :title="playlist.name"
            :subtitle="subtitle"
            @play="playFrom(0)"
            @shuffle="shufflePlay"
          >
            <template #actions>
              <Button
                :variant="editing ? 'brand' : 'background-strong'"
                @click="toggleEdit"
              >
                {{ editing ? t('musicLibrary.playlists.done') : t('musicLibrary.playlists.edit') }}
              </Button>
            </template>
          </TracklistHeader>

          <!-- Edit toolbar: rename + delete (two-tap confirm) + reorder hint. -->
          <div v-if="editing" class="edit-toolbar">
            <Button variant="background-strong" size="small" @click="renameOpen = true">
              {{ t('musicLibrary.playlists.rename') }}
            </Button>
            <Button variant="important" size="small" :loading="deleting" @click="handleDelete">
              {{ confirmDelete ? t('musicLibrary.playlists.confirmDelete') : t('musicLibrary.playlists.delete') }}
            </Button>
            <p v-if="tracks.length" class="edit-hint text-mono">{{ t('musicLibrary.playlists.reorderHint') }}</p>
          </div>

          <MessageContent v-if="!tracks.length" :title="t('musicLibrary.noTracks')" />

          <div v-else class="tracks" :class="{ reordering: editing }">
            <div
              v-for="(song, idx) in tracks"
              :key="song.id"
              class="drag-item"
              :class="{
                'drag-item--dragging': dragState.index === idx,
                'drag-item--transition': dragState.index !== -1 && dragState.index !== idx,
              }"
              :style="getDragItemStyle(idx)"
            >
              <TrackRow
                :song="song"
                :number="idx + 1"
                :current="song.id === store.currentTrackId"
                :playing="store.isPlaying"
                show-artist
                :show-menu="!editing"
                :editing="editing"
                show-cover
                :cover-url="store.thumbUrl(song.coverArt)"
                @play="playFrom(idx)"
                @menu="store.requestAddToPlaylist([song.id])"
                @remove="removeAt(idx)"
                @grip-down="onGripDown($event, idx)"
              />
            </div>
          </div>
        </div>
      </Transition>
    </div>

    <PlaylistNameModal
      :is-open="renameOpen"
      :title="t('musicLibrary.playlists.renameTitle')"
      :submit-label="t('musicLibrary.playlists.save')"
      :initial-name="playlist?.name || ''"
      :loading="renaming"
      @close="renameOpen = false"
      @submit="handleRename"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { totalMinutes } from '../format.js';
import MessageContent from '@/components/ui/MessageContent.vue';
import Button from '@/components/ui/Button.vue';
import TracklistHeader from '../cards/TracklistHeader.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import PlaylistNameModal from '../PlaylistNameModal.vue';

const props = defineProps({
  playlistId: {
    type: String,
    required: true,
  },
});

const emit = defineEmits(['deleted']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const playlist = ref(null);
const loading = ref(false);

// Local, mutable copy of the ordered entries — the edit surface (reorder/remove)
// works on this and persists the whole new order through setPlaylistTracks.
const tracks = ref([]);

const editing = ref(false);
const renameOpen = ref(false);
const renaming = ref(false);
const confirmDelete = ref(false);
const deleting = ref(false);

const subtitle = computed(() => {
  if (!playlist.value) return '';
  const parts = [t('musicLibrary.tracksCount', { count: tracks.value.length })];
  const mins = totalMinutes(playlist.value.duration);
  if (mins && !editing.value) parts.push(`${mins} ${t('musicLibrary.minutesShort')}`);
  return parts.join(' · ');
});

function playFrom(index) {
  store.playContext(tracks.value, index, false);
}

function shufflePlay() {
  if (!tracks.value.length) return;
  const start = Math.floor(Math.random() * tracks.value.length);
  store.playContext(tracks.value, start, true);
}

function toggleEdit() {
  editing.value = !editing.value;
  confirmDelete.value = false;
}

// === Rename ===
async function handleRename(name) {
  if (renaming.value) return;
  renaming.value = true;
  const ok = await store.renamePlaylist(props.playlistId, name);
  renaming.value = false;
  if (ok) {
    playlist.value = { ...playlist.value, name };
    renameOpen.value = false;
  }
}

// === Delete (two-tap inline confirm, like the share remove) ===
async function handleDelete() {
  if (deleting.value) return;
  if (!confirmDelete.value) {
    confirmDelete.value = true;
    return;
  }
  deleting.value = true;
  const ok = await store.deletePlaylist(props.playlistId);
  deleting.value = false;
  if (ok) emit('deleted');
  else confirmDelete.value = false;
}

// === Remove a track (persist the whole remaining order) ===
async function removeAt(index) {
  tracks.value.splice(index, 1);
  await persistOrder();
}

async function persistOrder() {
  const ok = await store.setPlaylistTracks(
    props.playlistId,
    tracks.value.map((s) => s.id)
  );
  // On failure, re-read the authoritative order from Navidrome.
  if (!ok) await reload();
}

// === Reorder (drag grip → swap, mirrors DockSettings) ===
const dragState = ref({ index: -1, startY: 0, currentY: 0, itemHeight: 0 });
let reordered = false;

function getDragItemStyle(index) {
  if (dragState.value.index !== index) return {};
  const offsetY = dragState.value.currentY - dragState.value.startY;
  return {
    transform: `translateY(${offsetY}px) scale(1.02)`,
    zIndex: 10,
    position: 'relative',
    transition: 'none',
  };
}

function onGripDown(event, index) {
  const wrapper = event.target.closest('.drag-item');
  dragState.value = {
    index,
    startY: event.clientY,
    currentY: event.clientY,
    itemHeight: wrapper ? wrapper.offsetHeight : 0,
  };
  reordered = false;
  document.addEventListener('pointermove', onDragMove);
  document.addEventListener('pointerup', onDragEnd);
  document.addEventListener('pointercancel', onDragEnd);
}

function onDragMove(event) {
  if (dragState.value.index === -1) return;
  dragState.value.currentY = event.clientY;
  const deltaY = event.clientY - dragState.value.startY;
  const threshold = dragState.value.itemHeight * 0.5;
  const dragged = dragState.value.index;

  if (deltaY > threshold && dragged < tracks.value.length - 1) {
    swap(dragged, dragged + 1);
    dragState.value.index = dragged + 1;
    dragState.value.startY += dragState.value.itemHeight;
    reordered = true;
  } else if (deltaY < -threshold && dragged > 0) {
    swap(dragged, dragged - 1);
    dragState.value.index = dragged - 1;
    dragState.value.startY -= dragState.value.itemHeight;
    reordered = true;
  }
}

function onDragEnd() {
  dragState.value = { index: -1, startY: 0, currentY: 0, itemHeight: 0 };
  removeDragListeners();
  if (reordered) {
    reordered = false;
    persistOrder();
  }
}

function swap(i, j) {
  const arr = tracks.value;
  [arr[i], arr[j]] = [arr[j], arr[i]];
}

function removeDragListeners() {
  document.removeEventListener('pointermove', onDragMove);
  document.removeEventListener('pointerup', onDragEnd);
  document.removeEventListener('pointercancel', onDragEnd);
}

// === Load ===
async function reload() {
  playlist.value = await store.fetchPlaylist(props.playlistId);
  tracks.value = playlist.value?.entry ? [...playlist.value.entry] : [];
}

watch(() => props.playlistId, async () => {
  loading.value = true;
  editing.value = false;
  confirmDelete.value = false;
  await reload();
  loading.value = false;
}, { immediate: true });

onUnmounted(removeDragListeners);
</script>

<style scoped>
.playlist-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.transition-container {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
}

.transition-container > * {
  grid-row: 1;
  grid-column: 1;
  align-self: start;
}

.content-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.edit-toolbar {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  flex-wrap: wrap;
}

.edit-hint {
  margin: 0;
  color: var(--color-text-secondary);
}

.tracks {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.drag-item {
  transition: transform var(--transition-spring);
}

.tracks.reordering .drag-item {
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.drag-item--dragging {
  z-index: 10;
  opacity: 0.92;
}

.drag-item--transition {
  transition: transform var(--transition-spring);
}
</style>
