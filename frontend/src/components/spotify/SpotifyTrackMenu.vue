<!-- frontend/src/components/spotify/SpotifyTrackMenu.vue -->
<!-- A track row's ⋯ menu in the Spotify browser: the pages the track leads
     to. It opens them by emitting; the source's navigation does the rest. -->
<template>
  <Dropdown v-if="mayLead" model-value="" :options="options" size="small" placement="top-end"
    icon-placement="start" @change="choose">
    <template #trigger="{ toggle, isOpen }">
      <button v-press type="button" class="track-menu-trigger" :aria-label="t('spotify.moreOptions')"
        :aria-busy="asking" @click="press(toggle, isOpen)">
        <SvgIcon name="threeDots" :size="20" />
      </button>
    </template>
  </Dropdown>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useTimer } from '@/composables/useTimer';
import Dropdown from '@/components/ui/Dropdown.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { albumKnownToHoldMore, canHaveRadio, menuArtists, trackMenuActions } from '@/utils/spotifyTrackMenu';

// How long a press waits for Spotify before the menu opens with what is known.
const ANSWER_WAIT_MS = 1500;
const ENTRIES = {
  radio: { label: 'spotify.goToSongRadio', icon: 'broadcast' },
  artist: { label: 'spotify.goToArtist', icon: 'userSound' },
  album: { label: 'spotify.goToAlbum', icon: 'vinylRecord' },
};

const props = defineProps({
  // A described track, as /contexts lists it.
  track: {
    type: Object,
    required: true,
  },
  // The page the row is on: 'playlist' | 'liked' | 'album' | 'artist'
  kind: {
    type: String,
    required: true,
  },
  // On an artist's page, its uri: that artist is not offered again.
  pageUri: {
    type: String,
    default: null,
  },
});

const emit = defineEmits(['artist', 'album', 'radio']);

const { t } = useI18n();
const store = useSpotifyStore();
const timer = useTimer();

// Whether the menu could hold anything, were every answer the best one.
const mayLead = computed(() => trackMenuActions(props.track, props.kind, {
  albumLength: Infinity,
  radioUri: canHaveRadio(props.track) ? 'not asked yet' : null,
  pageUri: props.pageUri,
}).length > 0);

// What the menu holds, settled before it opens: an entry arriving under the
// finger would move the one it was aimed at.
const shown = ref([]);
const radioUri = ref(null);
const asking = ref(false);
let alive = true;
onBeforeUnmount(() => { alive = false; });

// The artists the menu leads to. "Go to artist" where the track has only the
// one; otherwise each is named — on an artist's page, the one left is not the
// artist on screen.
const artists = computed(() => menuArtists(props.track, props.pageUri));

const options = computed(() => shown.value.flatMap((action) => {
  const { label, icon } = ENTRIES[action];
  if (action === 'artist' && props.track.artists.length > 1) {
    return artists.value.map((artist, index) => ({ value: `artist:${index}`, label: artist.name, icon }));
  }
  return [{ value: action, label: t(label), icon }];
}));

async function press(toggle, isOpen) {
  if (isOpen) {
    toggle();
    return;
  }
  if (asking.value) return;
  asking.value = true;
  const { track } = props;
  const answers = { albumLength: null, radioUri: null };
  const asks = [];
  if (canHaveRadio(track)) {
    asks.push(store.trackRadio(track.uri).then((uri) => { answers.radioUri = uri; }));
  }
  if (props.kind !== 'album' && track.album?.uri && !albumKnownToHoldMore(track)) {
    asks.push(store.contextLength(track.album.uri).then((length) => { answers.albumLength = length; }));
  }
  await Promise.race([Promise.all(asks), new Promise((resolve) => timer.setTimeout(resolve, ANSWER_WAIT_MS))]);
  asking.value = false;
  if (!alive) return;
  radioUri.value = answers.radioUri;
  shown.value = trackMenuActions(track, props.kind, { ...answers, pageUri: props.pageUri });
  if (shown.value.length) toggle();
}

function choose(action) {
  if (action === 'radio') emit('radio', { uri: radioUri.value, track: props.track });
  else if (action === 'artist') emit('artist', artists.value[0]);
  else if (action.startsWith('artist:')) emit('artist', artists.value[Number(action.slice('artist:'.length))]);
  else emit(action, props.track);
}
</script>

<style scoped>
.track-menu-trigger {
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
