<!--
  "Add to playlist" picker of the Spotify browser, hosted once at the source
  root and opened from any track row's ⋯ menu (store.requestAddToPlaylist).
  Liked Songs, then the playlists the account may edit, each a toggle that
  adds the track or removes it, as Music Library's picker does; a playlist
  already holding it shows checked, read fresh at each opening and after each
  removal (one copy goes, and a playlist may hold two). No row to
  create a playlist: go-librespot has no way to make one. Stays open across
  toggles; the Modal's own close button ends it.
-->
<template>
  <Modal :is-open="!!track" @close="store.closePlaylistPicker()">
    <div class="add-to-playlist">
      <NavigationHeader :title="t('spotify.addToPlaylist')" />

      <MessageContent v-if="failed" icon="spotify" :title="t('spotify.listUnavailable')"
        :cta-label="t('spotify.retry')" cta-variant="control" :cta-click="load" />

      <SectionCard v-else class="add-to-playlist__section">
        <template v-if="!answer">
          <SkeletonListItem v-for="n in 4" :key="n" subtitle="none" />
        </template>

        <template v-else>
          <ListItemButton
            v-if="answer.liked !== null"
            action="radio"
            icon-variant="standard"
            :title="t('spotify.likedSongs')"
            :model-value="answer.liked"
            :disabled="busy"
            @click="toggle(answer.liked_songs_uri, answer.liked)"
          >
            <template #icon>
              <SvgIcon name="heart" class="add-to-playlist__liked-heart" />
            </template>
          </ListItemButton>

          <ListItemButton
            v-for="playlist in answer.playlists"
            :key="playlist.uri"
            action="radio"
            :title="playlist.name || t('spotify.untitledPlaylist')"
            :model-value="!!playlist.contains"
            :disabled="busy || playlist.contains === null"
            @click="toggle(playlist.uri, playlist.contains)"
          >
            <template #icon>
              <LazyImage class="add-to-playlist__cover" :src="playlist.image" :fallback="musicPlaceholder"
                :alt="playlist.name || ''" lazy />
            </template>
          </ListItemButton>
        </template>
      </SectionCard>
    </div>
  </Modal>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import Modal from '@/components/ui/Modal.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SkeletonListItem from '@/components/ui/SkeletonListItem.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const { t } = useI18n();
const store = useSpotifyStore();

const track = computed(() => store.pickerTrack);
// The rows, as the route answered them, each toggled in place after a write.
const answer = ref(null);
const failed = ref(false);
const busy = ref(false);
// Moved by each opening: an answer for a track the picker is no longer open for is dropped.
let opening = 0;

// `keep`: the rows stay drawn while they are read again.
async function load({ keep = false } = {}) {
  const asked = ++opening;
  // As the playlists list it: a relinked track's own uri is not in them.
  const uri = track.value?.listed_uri ?? track.value?.uri;
  if (!keep) answer.value = null;
  failed.value = false;
  if (!uri) return;
  const data = await store.trackPlaylists(uri);
  if (asked !== opening) return;
  if (keep && !data) return;
  failed.value = !data;
  answer.value = data;
}

watch(track, (now) => {
  busy.value = false;
  if (now) load();
  else opening += 1;
});

async function toggle(uri, holds) {
  if (busy.value || !track.value) return;
  busy.value = true;
  const ok = holds
    ? await store.removeFromPlaylist(uri, track.value)
    : await store.addToPlaylist(uri, track.value);
  if (ok && holds) {
    // One copy is removed; a playlist holding the track twice still holds it.
    await load({ keep: true });
    busy.value = false;
    return;
  }
  busy.value = false;
  if (!ok || !answer.value) return;
  if (uri === answer.value.liked_songs_uri) {
    answer.value = { ...answer.value, liked: !holds };
  } else {
    answer.value = {
      ...answer.value,
      playlists: answer.value.playlists.map((p) => (p.uri === uri ? { ...p, contains: !holds } : p)),
    };
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
</style>
