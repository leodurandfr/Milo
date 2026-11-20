<!--
  "Add to playlist" picker, hosted once at the source root and opened from any
  track row's ⋯ menu via store.requestAddToPlaylist(songIds). A single view:
  existing playlists (radio toggles add/remove of the current songIds) plus
  an inline name+create row for a brand new one. Stays open across multiple
  adds — the user closes it via the Modal's own close button when done. Which
  playlists already hold the songs is read at each opening; until it is, no
  row can be toggled, and a read that fails says so with a Retry rather than
  draw every playlist unchecked — a tap would then add the songs twice. The
  rows are drawn from three reads (the playlists, the liked songs, which
  playlists hold the songs), so a failure of any of them is that state, and
  the Retry runs all three again.
-->
<template>
  <Modal :is-open="isOpen" @close="$emit('close')">
    <div class="add-to-playlist">
      <NavigationHeader :title="t('musicLibrary.playlists.addToPlaylist')">
        <template #actions>
          <IconButton icon="plus" :disabled="failed" @click="setCreateOpen(!showCreate)" />
        </template>
      </NavigationHeader>

      <MessageContent v-if="failed" icon="musicNote" :title="t('musicLibrary.catalogUnreachable')"
        :cta-label="t('musicLibrary.retry')" cta-variant="control"
        :cta-click="readChoices" />

      <SectionCard v-else class="add-to-playlist__section">
        <ListItemButton
          action="radio"
          icon-variant="standard"
          :title="t('musicLibrary.playlists.likedSongs')"
          :model-value="likedChecked"
          :disabled="busy || checkingExisting"
          @click="toggleLiked"
        >
          <template #icon>
            <SvgIcon name="heart" class="add-to-playlist__liked-heart" />
          </template>
        </ListItemButton>

        <ListItemButton
          v-for="pl in store.playlists"
          :key="pl.id"
          action="radio"
          :title="pl.name"
          :model-value="addedIds.has(pl.id)"
          :disabled="busy || checkingExisting"
          @click="toggleIn(pl)"
        >
          <template #icon>
            <LazyImage class="add-to-playlist__cover" :src="store.thumbUrl(pl.coverArt)" :fallback="musicPlaceholder" :alt="pl.name" lazy />
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
      </SectionCard>
    </div>
  </Modal>
</template>

<script setup>
import { ref, computed, watch, inject } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import Modal from '@/components/ui/Modal.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { musicPlaceholder } from '@/constants/placeholders';

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
let createdOnClose = false;
const showCreate = ref(false);
const addedIds = ref(new Set());
const checkingExisting = ref(false);
// A read the rows are drawn from did not answer: the rows are not drawn, a
// Retry is — and the header's create button, whose row is among them, is
// disabled.
const failed = ref(false);
// Moved by each opening and each closing: an answer read for an opening that
// is over is dropped — it would check the rows of other songs and unlock them
// before their own read is in. A write sent for an opening that is over is
// dropped the same way when it lands: its playlist holds the songs of that
// opening, not of this one.
let opening = 0;

const likedChecked = computed(() =>
  props.songIds.length > 0 && props.songIds.every((id) => store.likedSongIds.has(id))
);

async function toggleLiked() {
  if (busy.value) return;
  busy.value = true;
  const on = !likedChecked.value;
  for (const id of props.songIds) {
    await store.setSongFavorite(id, on);
  }
  busy.value = false;
}

const modalRequestHeightDelta = inject('modalRequestHeightDelta', null);
const modalContentInnerRef = inject('modalContentInnerRef', null);
const createExpandRef = ref(null);

function setCreateOpen(next) {
  // A create that went through closed the row on its name and its spinner, so
  // it folds as it was; it is emptied for the next one here.
  if (next && createdOnClose) {
    createdOnClose = false;
    creating.value = false;
    newName.value = '';
  }
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

// The three reads the rows are drawn from, at the opening and on Retry. The
// membership read alone was the Retry once: a Navidrome that failed it at the
// opening had failed the playlist list too, and a Retry that succeeded drew
// the membership over an empty list — no row for a playlist that holds the
// songs, and the create row inviting the user to make it again.
async function readChoices() {
  const asked = opening;
  checkingExisting.value = true;
  failed.value = false;
  const [listed, liked, held] = await Promise.all([
    store.loadPlaylists(),
    store.loadLikedSongs(),
    store.fetchPlaylistsContaining(props.songIds),
  ]);
  if (asked !== opening) return;
  if (held) addedIds.value = held;
  failed.value = !(listed && liked && held);
  checkingExisting.value = false;
}

// Fresh state + a current playlist list each time the picker opens — at mount
// too: the store keeps its songs while the source view is unmounted and mounted
// again, and a picker born open with no membership read would add them twice.
watch(() => props.isOpen, async (open) => {
  opening += 1;
  if (!open) return;
  newName.value = '';
  busy.value = false;
  creating.value = false;
  createdOnClose = false;
  showCreate.value = false;
  addedIds.value = new Set();
  await readChoices();
}, { immediate: true });

async function toggleIn(pl) {
  if (busy.value) return;
  const asked = opening;
  busy.value = true;
  const wasAdded = addedIds.value.has(pl.id);
  const ok = wasAdded
    ? await store.removeFromPlaylist(pl.id, props.songIds)
    : await store.addToPlaylist(pl.id, props.songIds);
  if (asked !== opening) return;
  busy.value = false;
  if (!ok) return;
  const next = new Set(addedIds.value);
  if (wasAdded) next.delete(pl.id);
  else next.add(pl.id);
  addedIds.value = next;
}

async function createAndAdd() {
  const name = newName.value.trim();
  if (!name || creating.value) return;
  const asked = opening;
  creating.value = true;
  const created = await store.createPlaylist(name, props.songIds);
  if (asked !== opening) return;
  if (created) {
    createdOnClose = true;
    setCreateOpen(false);
    addedIds.value = new Set(addedIds.value).add(created.id);
  } else {
    creating.value = false;
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

.add-to-playlist__liked-heart {
  color: var(--color-brand);
}

.add-to-playlist__section {
  overflow: hidden;
  gap: var(--space-02);
}

.add-to-playlist__expand {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-02));
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
