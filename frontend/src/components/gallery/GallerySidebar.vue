<!-- frontend/src/components/gallery/GallerySidebar.vue -->
<!--
  The component list: a filter and the 23 primitives under their group headings,
  one selected at a time.

  Selecting instead of scrolling is the whole fix for the first version of this
  page, which showed a group's worth of cards at once and ran to four screens for
  four components.
-->
<template>
  <nav class="sidebar">
    <div class="sidebar__head">
      <a class="sidebar__back text-mono-small" href="/">← Milō</a>
      <h1 class="heading-4">Components</h1>
    </div>

    <input
      class="sidebar__filter text-mono-small"
      type="search"
      :value="query"
      placeholder="Filter"
      @input="$emit('update:query', $event.target.value)"
    >

    <p v-if="!visibleGroups.length" class="sidebar__empty text-mono-small">
      Nothing matches “{{ query }}”.
    </p>

    <div v-for="group in visibleGroups" :key="group.id" class="sidebar__group">
      <h2 class="sidebar__group-title text-mono-small">{{ group.title }}</h2>
      <ul class="sidebar__list">
        <li v-for="entry in group.entries" :key="entry.id">
          <button
            v-press
            type="button"
            class="sidebar__item text-mono-small"
            :class="{ 'sidebar__item--active': entry.id === selected }"
            @click="$emit('select', entry.id)"
          >
            {{ entry.label ?? entry.id }}
          </button>
        </li>
      </ul>
    </div>
  </nav>
</template>

<script setup>
import { computed } from 'vue';
import { GROUPS, entriesOf } from './catalog';
import { AUDIO_SOURCES_ID } from './registry';
import { SOURCE_PAGES } from './sources';

/**
 * The second axis, first in the list, and one row rather than ten: the source
 * is a select on the page itself, so the list stays a list of *things to open*
 * instead of gaining a second, longer section that says the same word ten
 * times. It sits at the top because it is the coarser question — "what does
 * Spotify look like" is where a reader starts, and "what does AudioPlayerFull
 * take" is where they end up.
 *
 * Filtering matches the source names too, so typing "spot" still finds it —
 * the row is the way in, and a reader searching for a source should not have
 * to know that.
 */
const SOURCE_NAMES = SOURCE_PAGES.map(page => page.title.toLowerCase());

const SOURCE_GROUP = {
  id: 'sources',
  title: 'Audio sources',
  pages: [{ id: AUDIO_SOURCES_ID, label: 'Audio sources' }]
};

const props = defineProps({
  /** Catalogue id currently open in the canvas. */
  selected: {
    type: String,
    default: ''
  },
  /** Filter text, owned by the page so it survives in one place. */
  query: {
    type: String,
    default: ''
  }
});

defineEmits(['select', 'update:query']);

const visibleGroups = computed(() => {
  const needle = props.query.trim().toLowerCase();

  const matchesSources = !needle
    || SOURCE_GROUP.pages.some(page => page.label.toLowerCase().includes(needle))
    || SOURCE_NAMES.some(name => name.includes(needle));

  const sources = {
    id: SOURCE_GROUP.id,
    title: SOURCE_GROUP.title,
    entries: matchesSources ? SOURCE_GROUP.pages : []
  };

  const components = GROUPS
    .map(group => ({
      ...group,
      entries: entriesOf(group.id).filter(entry => !needle || entry.id.toLowerCase().includes(needle))
    }));

  return [sources, ...components].filter(group => group.entries.length);
});
</script>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  height: 100%;
  padding: var(--space-03);
  overflow-y: auto;
  background: var(--color-background-neutral);
}

.sidebar__head {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.sidebar__back {
  color: var(--color-text-light);
  text-decoration: none;
}

.sidebar__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.sidebar__group-title {
  color: var(--color-text-light);
  text-transform: uppercase;
}

.sidebar__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  margin: 0;
  padding: 0;
  list-style: none;
}

/* Compact rows rather than `Button`: 23 names at the app's finger-sized 18 px
   would not be a list you can scan. `v-press` keeps the one press affordance the
   codebase has. */
.sidebar__item {
  display: block;
  width: 100%;
  padding: var(--space-01) var(--space-02);
  text-align: left;
  color: var(--color-text-secondary);
  background: transparent;
  border: 0;
  border-radius: var(--radius-02);
  cursor: pointer;
}

.sidebar__item--active {
  color: var(--color-text-contrast);
  background: var(--color-brand);
}

.sidebar__filter {
  width: 100%;
  box-sizing: border-box;
  padding: var(--space-01) var(--space-02);
  color: var(--color-text);
  background: var(--color-background-strong);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
}

.sidebar__filter:focus {
  outline: 1px solid var(--color-brand);
}

.sidebar__empty {
  color: var(--color-text-light);
}
</style>
