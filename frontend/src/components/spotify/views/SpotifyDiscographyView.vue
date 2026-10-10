<template>
  <div class="spotify-discography">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <MessageContent v-if="!groups.length && error" key="error" icon="spotify"
          :title="error === 'not_signed_in' ? t('spotify.signingIn') : t('spotify.listUnavailable')"
          :cta-label="t('spotify.retry')" cta-variant="control" :cta-click="load" />

        <div v-else-if="!groups.length" key="loading" class="content-stack swap-skeleton" aria-hidden="true">
          <span class="skeleton-title-line heading-2">
            <span class="skeleton-text-line shimmer skeleton-title"></span>&#8203;
          </span>
          <div class="skeleton-groups shimmer"></div>
          <div class="cards-grid">
            <SkeletonSpotifyCard v-for="i in columns * 2" :key="i" />
          </div>
        </div>

        <div v-else key="loaded" class="content-stack">
          <h2 v-if="page.name" class="artist-name heading-2">{{ page.name }}</h2>
          <ButtonGroup v-if="groups.length > 1" :model-value="current.id" :options="options"
            size="small" mobile-layout="scroll"
            @update:model-value="state.group = $event" />
          <SpotifyCardGrid :items="cards" @select="$emit('select', $event)" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, reactive, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { NAVIGATION_ENTRY_STATE } from '@/composables/useNavigationStack';
import MessageContent from '@/components/ui/MessageContent.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import SpotifyCardGrid from '../SpotifyCardGrid.vue';
import SkeletonSpotifyCard from '../cards/SkeletonSpotifyCard.vue';
import { useSpotifyDiscography } from '@/composables/useSpotifyDiscography';
import { useCardGridColumns } from '@/composables/useCardGridColumns';

const props = defineProps({
  // The artist whose discography this is.
  uri: {
    type: String,
    required: true,
  },
  // The list picked on the artist's page, shown first.
  group: {
    type: String,
    default: '',
  },
});

// A release to open.
defineEmits(['select']);

const { t } = useI18n();
const store = useSpotifyStore();
const { columns } = useCardGridColumns();

// The list picked here, kept in the navigation entry for the way back.
const state = inject(NAVIGATION_ENTRY_STATE, null)?.() ?? reactive({});

const page = computed(() => store.artists[props.uri] ?? {});
const error = computed(() => store.artistErrors[props.uri] ?? null);
// The discography is the one section made of lists.
const groups = computed(() => (page.value.sections ?? []).find((s) => s.groups)?.groups ?? []);
const { current, options, cards } = useSpotifyDiscography(() => groups.value, () => state.group ?? props.group);

function load() {
  store.loadArtist(props.uri, { force: !!error.value });
}

// The store forgot the page (another language): asked again in this one.
watch(() => store.artists[props.uri], (now, before) => {
  if (before && !now) load();
});

load();
</script>

<style scoped>
.spotify-discography {
  display: flex;
  flex-direction: column;
}

.content-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.artist-name {
  color: var(--color-text);
  margin: 0;
}

.skeleton-title-line {
  display: flex;
  align-items: center;
}

.skeleton-title {
  width: 30%;
}

/* A small ButtonGroup's track, as the loaded view draws one. */
.skeleton-groups {
  width: 280px;
  max-width: 100%;
  height: 40px;
  border-radius: var(--radius-05);
}

@media (max-aspect-ratio: 4/3) {
  .skeleton-groups {
    height: 38px;
    border-radius: var(--radius-04);
  }
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(var(--card-grid-columns), minmax(0, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-03);
}
</style>
