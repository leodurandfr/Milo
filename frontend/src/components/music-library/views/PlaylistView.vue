<template>
  <div class="playlist-view">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <SkeletonDetailPage v-if="loading && !playlist" key="loading" cover artist />
        <MessageContent v-else-if="!playlist" key="notfound" icon="musicNote" :title="t('musicLibrary.notFound')" />

        <div v-else key="loaded" class="content-stack">
          <DetailHeader
            :image-src="store.coverUrl(playlist.coverArt, 600)"
            :fallback="musicPlaceholder"
            :title="playlist.name"
            :subtitle-meta="subtitle"
            :playing="header.playing.value"
            :play-loading="header.loading.value"
            @play="header.play"
            @pause="header.pause"
          >
            <template #actions>
              <IconButton
                v-if="editing"
                icon="threeDots"
                variant="brand"
                size="medium"
                :aria-label="t('musicLibrary.playlists.done')"
                @click="toggleEdit"
              />
              <LibraryItemMenu v-else trigger="header" :songs="() => tracks" :shuffle="tracks.length > 0" edit
                @edit="toggleEdit" @shuffle="header.shuffle" />
            </template>
          </DetailHeader>

          <!-- Edit toolbar: rename + delete (two-tap confirm) + reorder hint. -->
          <div v-if="editing" class="edit-toolbar">
            <Button variant="control" size="small" @click="renameOpen = true">
              {{ t('musicLibrary.playlists.rename') }}
            </Button>
            <Button variant="important" size="small" :loading="deleting" @click="handleDelete">
              {{ confirmDelete ? t('musicLibrary.playlists.confirmDelete') : t('musicLibrary.playlists.delete') }}
            </Button>
            <p v-if="tracks.length" class="edit-hint text-mono-medium">{{ t('musicLibrary.playlists.reorderHint') }}</p>
          </div>

          <MessageContent v-if="!tracks.length" icon="musicNote" :title="t('musicLibrary.noTracks')" />

          <TrackList v-else :class="{ reordering: editing }">
            <div
              v-for="(song, idx) in dragRows"
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
                :editing="editing"
                show-cover
                :cover-url="store.thumbUrl(song.coverArt)"
                @play="playFrom(idx)"
                @remove="removeAt(idx)"
                @grip-down="onGripDown($event, idx)"
              >
                <template #menu>
                  <LibraryItemMenu :songs="() => [song]" add-to-playlist />
                </template>
              </TrackRow>
            </div>
            <div v-if="hasMore" ref="sentinelRef" aria-hidden="true"></div>
          </TrackList>
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
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { totalMinutes } from '../format.js';
import MessageContent from '@/components/ui/MessageContent.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import SkeletonDetailPage from '@/components/audio/SkeletonDetailPage.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import TrackList from '@/components/audio/TrackList.vue';
import PlaylistNameModal from '../PlaylistNameModal.vue';
import LibraryItemMenu from '../LibraryItemMenu.vue';
import { musicPlaceholder } from '@/constants/placeholders';
import { useRenderWindow } from '@/composables/useRenderWindow';
import { useDetailPlayback } from '@/composables/useDetailPlayback';
import { useDragReorder } from '@/composables/useDragReorder';

const props = defineProps({
  playlistId: {
    type: String,
    required: true,
  },
});

const emit = defineEmits(['deleted']);

const { t, formatUnit } = useI18n();
const store = useMusicLibraryStore();

const playlist = ref(null);
const loading = ref(false);

// Local, mutable copy of the ordered entries — the edit surface (reorder/remove)
// works on this and persists its new order through setPlaylistTracks, with the
// order the edit was made from. It holds only what a mounted storage space
// serves; the backend keeps the rest in place.
const tracks = ref([]);
const trackIds = () => tracks.value.map((s) => s.id);
const { visible: visibleTracks, hasMore, sentinelRef } = useRenderWindow(tracks);

const editing = ref(false);
const renameOpen = ref(false);
const renaming = ref(false);
const confirmDelete = ref(false);
const deleting = ref(false);

const subtitle = computed(() => {
  if (!playlist.value) return '';
  const parts = [t('musicLibrary.tracksCount', { count: tracks.value.length })];
  const mins = totalMinutes(playlist.value.duration);
  if (mins && !editing.value) parts.push(formatUnit(mins, 'min'));
  return parts.join(' · ');
});

const contextId = computed(() => `playlist:${props.playlistId}`);

function playFrom(index) {
  return store.playContext(tracks.value, index, false, contextId.value);
}

const header = useDetailPlayback(
  'music_library', () => store.currentContextId === contextId.value,
  (shuffle) => (shuffle ? store.playShuffled(tracks.value, contextId.value) : playFrom(0)),
);

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

// === Remove a track (persist the order of the rows left) ===
async function removeAt(index) {
  const shown = trackIds();
  tracks.value.splice(index, 1);
  await persistOrder(shown);
}

async function persistOrder(shown) {
  const ok = await store.setPlaylistTracks(props.playlistId, shown, trackIds());
  // On failure, re-read the authoritative order from Navidrome.
  if (!ok) await reload();
}

// === Reorder (drag grip; the dragged row moves among the mounted ones) ===
const { rows: dragRows, dragState, getDragItemStyle, onGripDown } = useDragReorder(visibleTracks, {
  onDrop(from, to) {
    const shown = trackIds();
    const [moved] = tracks.value.splice(from, 1);
    tracks.value.splice(to, 0, moved);
    persistOrder(shown);
  },
});

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
</script>

<style scoped>
.playlist-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
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

.drag-item {
  transition: transform var(--transition-spring);
}

.reordering .drag-item {
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
