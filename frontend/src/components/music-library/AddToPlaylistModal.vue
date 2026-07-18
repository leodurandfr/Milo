<!--
  "Add to playlist" picker, hosted once at the source root and opened from any
  track row's ⋯ menu via store.requestAddToPlaylist(songIds). Two modes:
  - pick   — choose an existing playlist (appends the songs), or
  - create — name a new playlist seeded with those songs.
  The store owns the open/close state (addToPlaylistSongIds); this component
  performs the write and closes on success.
-->
<template>
  <Modal :is-open="isOpen" @close="$emit('close')">
    <div class="add-to-playlist">
      <h2 class="heading-2 add-to-playlist__title">{{ t('musicLibrary.playlists.addToPlaylist') }}</h2>

      <template v-if="mode === 'pick'">
        <ListItemButton
          variant="background"
          :title="t('musicLibrary.playlists.newPlaylist')"
          :disabled="busy"
          @click="mode = 'create'"
        >
          <template #icon>
            <div class="add-to-playlist__new-icon">
              <SvgIcon name="plus" :size="24" />
            </div>
          </template>
        </ListItemButton>

        <ListItemButton
          v-for="pl in store.playlists"
          :key="pl.id"
          variant="background"
          :title="pl.name"
          :subtitle="t('musicLibrary.songsCount', { count: pl.songCount || 0 })"
          :disabled="busy"
          @click="addTo(pl)"
        >
          <template #icon>
            <LazyImage :src="store.thumbUrl(pl.coverArt)" :fallback="albumPlaceholder" :alt="pl.name" lazy />
          </template>
        </ListItemButton>
      </template>

      <template v-else>
        <InputText
          v-model="newName"
          :placeholder="t('musicLibrary.playlists.namePlaceholder')"
          :maxlength="255"
          @submit="createAndAdd"
        />
        <div class="add-to-playlist__actions">
          <Button variant="background-strong" :disabled="busy" @click="mode = 'pick'">
            {{ t('musicLibrary.playlists.cancel') }}
          </Button>
          <Button variant="brand" :loading="busy" :disabled="busy || !newName.trim()" @click="createAndAdd">
            {{ t('musicLibrary.playlists.create') }}
          </Button>
        </div>
      </template>
    </div>
  </Modal>
</template>

<script setup>
import { ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import Modal from '@/components/ui/Modal.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';

const props = defineProps({
  isOpen: {
    type: Boolean,
    default: false,
  },
  // Song ids to add (never empty while open).
  songIds: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['close']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const mode = ref('pick');
const newName = ref('');
const busy = ref(false);

// Fresh state + a current playlist list each time the picker opens.
watch(() => props.isOpen, (open) => {
  if (!open) return;
  mode.value = 'pick';
  newName.value = '';
  busy.value = false;
  store.loadPlaylists();
});

async function addTo(pl) {
  if (busy.value) return;
  busy.value = true;
  const ok = await store.addToPlaylist(pl.id, props.songIds);
  busy.value = false;
  if (ok) emit('close');
}

async function createAndAdd() {
  const name = newName.value.trim();
  if (!name || busy.value) return;
  busy.value = true;
  const created = await store.createPlaylist(name, props.songIds);
  busy.value = false;
  if (created) emit('close');
}
</script>

<style scoped>
.add-to-playlist {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.add-to-playlist__title {
  margin: 0 0 var(--space-02);
  color: var(--color-text);
}

/* Match the 40x40 cover thumbnails on the playlist rows. */
.add-to-playlist__new-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  background: var(--color-background-neutral-50);
  color: var(--color-brand);
}

.add-to-playlist__actions {
  display: flex;
  flex-direction: row;
  gap: var(--space-02);
  margin-top: var(--space-02);
}

.add-to-playlist__actions .btn {
  flex: 1;
}
</style>
