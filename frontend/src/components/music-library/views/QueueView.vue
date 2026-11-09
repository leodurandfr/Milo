<template>
  <div class="queue-view">
    <ButtonGroup :model-value="tab" :options="options" size="small"
      @update:model-value="state.tab = $event" />

    <div class="swap-stack">
      <Transition name="fade-slide">
        <MessageContent v-if="!rows.length && tab === 'queue'" key="queue-empty" icon="queue"
          :title="t('musicLibrary.queueEmpty')" :subtitle="t('musicLibrary.queueEmptyHint')" />

        <MessageContent v-else-if="!rows.length" key="played-empty" icon="queue"
          :title="t('musicLibrary.previouslyPlayedEmpty')" />

        <TrackList v-else ref="listRef" :key="tab" class="queue-list">
          <div v-if="line" class="drop-line" aria-hidden="true"
            :style="{ top: `${line.top}px`, left: `${line.left}px`, width: `${line.width}px` }"></div>
          <div
            v-for="(row, idx) in visibleRows"
            :key="row.key"
            data-hold-row
            :class="{ movable: canMove(idx), lifted: lifted === idx }"
            :style="rowStyle(idx)"
            @pointerdown="onPointerDown($event, idx)"
          >
            <TrackRow
              :song="row.song"
              :number="idx + 1"
              :current="row.current"
              :playing="store.isPlaying"
              show-artist
              show-cover
              :cover-url="store.thumbUrl(row.song.coverArt)"
              @play="store.playIndex(row.queueIndex)"
            >
              <template #menu>
                <QueueTrackMenu :removable="store.canRemoveFromQueue && !row.current"
                  @playlist="store.requestAddToPlaylist([row.song.id])"
                  @remove="store.removeFromQueue(row.queueIndex, row.song.id)" />
              </template>
            </TrackRow>
          </div>
          <div v-if="hasMore" ref="sentinelRef" aria-hidden="true"></div>
        </TrackList>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, reactive, ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { NAVIGATION_ENTRY_STATE } from '@/composables/useNavigationStack';
import { useRenderWindow } from '@/composables/useRenderWindow';
import { useHoldReorder } from '@/composables/useHoldReorder';
import { useHeldOrder } from '@/composables/useHeldOrder';
import MessageContent from '@/components/ui/MessageContent.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import TrackList from '@/components/audio/TrackList.vue';
import QueueTrackMenu from '../QueueTrackMenu.vue';
import { moveRow, queueMove, splitQueue } from '../queueRows.js';

const { t } = useI18n();
const store = useMusicLibraryStore();

// The list picked here, kept in the navigation entry for the way back.
const state = inject(NAVIGATION_ENTRY_STATE, null)?.() ?? reactive({});
const tab = computed(() => state.tab ?? 'queue');
const options = computed(() => [
  { label: t('musicLibrary.queue'), value: 'queue' },
  { label: t('musicLibrary.previouslyPlayed'), value: 'played' },
]);

const split = computed(() => splitQueue(store.queue, store.queueIndex));

// A state republished for anything but the queue (a pause) keeps a dropped
// row where it was dropped.
const queueShape = () => `${store.queueIndex}:${store.queue.map((song) => song.id).join(',')}`;
const { held: pendingUpcoming, hold } = useHeldOrder(queueShape);

const rows = computed(() =>
  tab.value === 'played' ? split.value.played : (pendingUpcoming.value ?? split.value.upcoming));
const { visible: visibleRows, hasMore, sentinelRef } = useRenderWindow(() => rows.value);

// The row playing can neither move nor be crossed: nothing lands above it.
const canMove = (index) =>
  tab.value === 'queue' && store.canMoveInQueue && !visibleRows.value[index]?.current;

const listRef = ref(null);
const { lifted, line, rowStyle, onPointerDown, cancel } = useHoldReorder({
  list: () => listRef.value?.$el,
  canMove,
  minGap: () => visibleRows.value.findIndex((row) => row.current) + 1,
  onDrop(from, to) {
    const row = visibleRows.value[from];
    const { fromIndex, toIndex } = queueMove(row, from, to);
    hold(moveRow(rows.value, from, to), store.moveInQueue(fromIndex, toIndex, row.song.id));
  },
});

// A lift works on the rows as they were drawn when it began: an order that
// moved since (a new state, a refused drop let go) would land another track.
// The whole list, not the render window, which only grows at the end.
const rowOrder = () => rows.value.map((row) => row.key).join(',');
watch([rowOrder, () => store.canMoveInQueue], cancel);
</script>

<style scoped>
.queue-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.queue-list {
  position: relative;
}

.movable {
  touch-action: pan-y;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}

.lifted {
  position: relative;
  z-index: 1;
  background: var(--color-surface-solid);
  border-radius: var(--radius-03);
  box-shadow: var(--shadow-raised-01);
}

.drop-line {
  position: absolute;
  z-index: 2;
  height: calc(var(--space-01) / 2);
  border-radius: var(--radius-full);
  background: var(--color-brand);
  transform: translateY(-50%);
  pointer-events: none;
}
</style>
