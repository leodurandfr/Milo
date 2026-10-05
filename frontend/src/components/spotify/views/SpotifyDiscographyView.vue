<template>
  <div class="spotify-discography">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <MessageContent v-if="!groups.length && error" key="error" icon="network"
          :title="error === 'not_signed_in' ? t('spotify.signingIn') : t('spotify.listUnavailable')"
          :cta-label="t('spotify.retry')" cta-variant="control" :cta-click="load" />

        <MessageContent v-else-if="!groups.length" key="loading" loading />

        <div v-else key="loaded" class="content-stack">
          <h2 v-if="page.name" class="artist-name heading-2">{{ page.name }}</h2>
          <ButtonGroup v-if="groups.length > 1" :model-value="current.id" :options="options"
            size="small" inactive-variant="surface" mobile-layout="scroll"
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
import { useSpotifyDiscography } from '@/composables/useSpotifyDiscography';

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
</style>
