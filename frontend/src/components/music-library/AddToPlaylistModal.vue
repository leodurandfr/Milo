<!--
  "Add to playlist" picker, hosted once at the source root and opened from any
  track row's ⋯ menu via store.requestAddToPlaylist(songIds). A single view:
  existing playlists (radio fills once the songs have been added to it) plus
  an inline name+create row for a brand new one. Stays open across multiple
  adds — the user closes it via the Modal's own close button when done.
-->
<template>
  <Modal :is-open="isOpen" @close="$emit('close')">
    <div class="add-to-playlist">
      <NavigationHeader :title="t('musicLibrary.playlists.addToPlaylist')">
        <template #actions>
          <IconButton icon="plus" variant="on-dark" @click="setCreateOpen(!showCreate)" />
        </template>
      </NavigationHeader>

      <SettingsSection class="add-to-playlist__section">
        <ListItemButton
          v-for="pl in store.playlists"
          :key="pl.id"
          variant="background"
          action="radio"
          :title="pl.name"
          :model-value="addedIds.has(pl.id)"
          :disabled="busy || addedIds.has(pl.id)"
          @click="addTo(pl)"
        >
          <template #icon>
            <LazyImage class="add-to-playlist__cover" :src="store.thumbUrl(pl.coverArt)" :fallback="albumPlaceholder" :alt="pl.name" lazy />
          </template>
        </ListItemButton>

        <div ref="createExpandRef" class="add-to-playlist__expand" :class="{ 'is-open': showCreate }">
          <div class="add-to-playlist__expand-inner">
            <div class="add-to-playlist__divider"></div>

            <div class="add-to-playlist__create">
              <InputText
                v-model="newName"
                :placeholder="t('musicLibrary.playlists.namePlaceholder')"
                :maxlength="255"
                @submit="createAndAdd"
              />
              <Button variant="brand" left-icon="plus" :loading="creating" :disabled="creating || !newName.trim()" @click="createAndAdd">
                {{ t('musicLibrary.playlists.create') }}
              </Button>
            </div>
          </div>
        </div>
      </SettingsSection>
    </div>
  </Modal>
</template>

<script setup>
import { ref, watch, inject } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import Modal from '@/components/ui/Modal.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
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

const newName = ref('');
const busy = ref(false);
const creating = ref(false);
const showCreate = ref(false);
const addedIds = ref(new Set());

const modalRequestHeightDelta = inject('modalRequestHeightDelta', null);
const modalContentInnerRef = inject('modalContentInnerRef', null);
const createExpandRef = ref(null);

function setCreateOpen(next) {
  if (modalRequestHeightDelta && createExpandRef.value && modalContentInnerRef?.value) {
    const el = createExpandRef.value;
    const before = modalContentInnerRef.value.offsetHeight;
    el.style.transition = 'none';
    el.classList.toggle('is-open', next);
    el.offsetHeight;
    const after = modalContentInnerRef.value.offsetHeight;
    el.classList.toggle('is-open', showCreate.value);
    el.offsetHeight;
    el.style.transition = '';
    modalRequestHeightDelta(after - before);
  }
  showCreate.value = next;
}

// Fresh state + a current playlist list each time the picker opens.
watch(() => props.isOpen, (open) => {
  if (!open) return;
  newName.value = '';
  busy.value = false;
  creating.value = false;
  showCreate.value = false;
  addedIds.value = new Set();
  store.loadPlaylists();
});

async function addTo(pl) {
  if (busy.value || addedIds.value.has(pl.id)) return;
  busy.value = true;
  const ok = await store.addToPlaylist(pl.id, props.songIds);
  busy.value = false;
  if (ok) addedIds.value = new Set(addedIds.value).add(pl.id);
}

async function createAndAdd() {
  const name = newName.value.trim();
  if (!name || creating.value) return;
  creating.value = true;
  const created = await store.createPlaylist(name, props.songIds);
  creating.value = false;
  if (created) {
    newName.value = '';
    setCreateOpen(false);
    addedIds.value = new Set(addedIds.value).add(created.id);
  }
}
</script>

<style scoped>
.add-to-playlist {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.add-to-playlist__cover {
  width: 100%;
  height: 100%;
}

.add-to-playlist__section {
  overflow: hidden;
}

.add-to-playlist__expand {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-04));
  transition:
    grid-template-rows var(--transition-fast),
    opacity var(--transition-fast),
    margin-top var(--transition-fast);
}

.add-to-playlist__expand.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
  margin-top: 0;
}

.add-to-playlist__expand-inner {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.add-to-playlist__divider {
  height: 1px;
  background: var(--color-border);
}

.add-to-playlist__create {
  display: flex;
  flex-direction: row;
  gap: var(--space-02);
  align-items: center;
}
</style>
