<template>
  <!-- The rows shown, then the next one peeking under the fade (its cover
       only): there is more below. -->
  <ShowMoreClip :peek-index="rows * columns" :has-more="hasMore" :peek-extent="cover"
    :items="items" :columns="columns" @more="state[stateKey] = rows + MORE_ROWS">
    <SpotifyCardGrid :items="rendered" @select="$emit('select', $event)" />
  </ShowMoreClip>
</template>

<script setup>
import { computed, inject, reactive } from 'vue';
import { useCardGridColumns } from '@/composables/useCardGridColumns';
import { NAVIGATION_ENTRY_STATE } from '@/composables/useNavigationStack';
import ShowMoreClip from './ShowMoreClip.vue';
import SpotifyCardGrid from './SpotifyCardGrid.vue';

const props = defineProps({
  // Library playlists, as /api/spotify/home lists them.
  items: {
    type: Array,
    required: true,
  },
  // Names this grid's row count in the navigation entry, for a view with two.
  stateKey: {
    type: String,
    required: true,
  },
});

defineEmits(['select']);

const FIRST_ROWS = 2;
const MORE_ROWS = 5;

const { columns } = useCardGridColumns();

// Kept in the navigation entry: going back to the home remounts it and
// restores its scroll, which must find the rows it was scrolled to.
const state = inject(NAVIGATION_ENTRY_STATE, null)?.() ?? reactive({});
const rows = computed(() => state[props.stateKey] ?? FIRST_ROWS);

const hasMore = computed(() => props.items.length > rows.value * columns.value);
// The rows shown and, while there is more, the row that peeks under the fade.
const rendered = computed(() =>
  (hasMore.value ? props.items.slice(0, (rows.value + 1) * columns.value) : props.items)
);

// A card's cover is square: all of it shows of the peeking row.
const cover = (card) => card.offsetWidth;
</script>

