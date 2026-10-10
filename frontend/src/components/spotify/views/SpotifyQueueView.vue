<template>
  <div class="spotify-queue">
    <ButtonGroup :model-value="tab" :options="options" size="small"
      @update:model-value="state.tab = $event" />

    <div class="swap-stack">
      <Transition name="fade-slide">
        <MessageContent v-if="tab === 'recent' && store.historyError && !rows.length" key="recent-error"
          icon="network" :title="t('spotify.listUnavailable')"
          :cta-label="t('spotify.retry')" cta-variant="control" :cta-click="store.loadHistory" />

        <MessageContent v-else-if="tab === 'recent' && !historyRead" key="recent-loading" loading />

        <MessageContent v-else-if="!rows.length" :key="`${tab}-empty`" icon="queue"
          :title="tab === 'recent' ? t('spotify.recentlyPlayedEmpty') : t('spotify.queueEmpty')" />

        <TrackList v-else :key="tab">
          <template v-for="(row, idx) in visibleRows" :key="row.key">
            <!-- An entry go-librespot has not described yet: named within a
                 second (its window prefetch), it holds its place until then. -->
            <SkeletonTrackRow v-if="!row.song.title" cover artist />
            <TrackRow
              v-else
              :song="row.song"
              :number="idx + 1"
              :current="row.current"
              :playing="store.isPlaying"
              show-artist
              show-cover
              :cover-url="row.track.thumbnail || ''"
              @play="play(row)"
            />
          </template>
          <div v-if="hasMore" ref="sentinelRef" aria-hidden="true"></div>
        </TrackList>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, reactive, ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { usePlayerControls } from '@/composables/usePlayerControls';
import { NAVIGATION_ENTRY_STATE } from '@/composables/useNavigationStack';
import { useRenderWindow } from '@/composables/useRenderWindow';
import MessageContent from '@/components/ui/MessageContent.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import SkeletonTrackRow from '@/components/audio/SkeletonTrackRow.vue';
import TrackList from '@/components/audio/TrackList.vue';

const { t } = useI18n();
const store = useSpotifyStore();
const { sendSourceCommand } = usePlayerControls('spotify');

// The list picked here, kept in the navigation entry for the way back.
const state = inject(NAVIGATION_ENTRY_STATE, null)?.() ?? reactive({});
const tab = computed(() => state.tab ?? 'queue');
const options = computed(() => [
  { label: t('spotify.queue'), value: 'queue' },
  { label: t('spotify.recentlyPlayed'), value: 'recent' },
]);

const seconds = (ms) => (ms ? ms / 1000 : 0);

// The queue: the track playing, then what follows. Keyed by the entry and its
// occurrence (a track can be queued twice), never by position: one track on,
// every position moves, and the rows would all be built again.
const queueRows = computed(() => {
  const seen = new Map();
  return store.upNext.map((track, idx) => {
    const nth = (seen.get(track.uri) ?? 0) + 1;
    seen.set(track.uri, nth);
    return {
      key: `${track.uri}#${nth}`,
      kind: 'queue',
      track,
      song: { title: track.title, artist: track.artist, duration: seconds(track.duration_ms) },
      current: idx === 0,
    };
  });
});

// The history lists a track as it starts: the one playing is the queue's.
// /status names a relinked track by its own uri, the history by the one its
// context lists, so both are compared.
const playingNow = (track) =>
  !!store.currentTrackUri && [track.uri, track.track_uri].includes(store.currentTrackUri);
const historyRows = computed(() => store.history
  .filter((track, idx) => !(idx === 0 && playingNow(track)))
  .map((track) => ({
    key: `${track.uri}-${track.played_at}`,
    kind: 'history',
    track,
    song: { title: track.title, artist: track.artist, duration: seconds(track.duration_ms) },
    current: false,
  })));

const rows = computed(() => (tab.value === 'recent' ? historyRows.value : queueRows.value));
// Up to 500 rows of history: mounted a chunk at a time.
const { visible: visibleRows, hasMore, sentinelRef } = useRenderWindow(() => rows.value);

// Read when the tab shows, again at each new track, the one moment the
// history moves, and for another account signed in, which is loading again
// rather than an empty history.
const historyRead = ref(false);
watch([tab, () => store.currentTrackUri, () => store.account], async ([now], [, , accountBefore] = []) => {
  if (now !== 'recent') return;
  if (store.account !== accountBefore) historyRead.value = false;
  await store.loadHistory();
  historyRead.value = true;
}, { immediate: true });

// The queue's rows are transport, sent only when the source lists the
// command now (`controls`): `next` to an entry is looked for in the queue,
// then through the whole context. The history plays content, as the
// browser's lists do.
function play(row) {
  if (row.kind === 'history') store.playFromHistory(row.track);
  else if (!row.current) sendSourceCommand('next', { uri: row.track.uri });
  else if (!store.isPlaying) sendSourceCommand('resume');
}
</script>

<style scoped>
.spotify-queue {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}
</style>
