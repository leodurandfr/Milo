<template>
  <!-- An artist's discography, as Spotify's desktop app draws it: one section,
       a list per kind of release to pick beside its title, the picked one
       sideways; the title opens the picked list whole. -->
  <section class="discography">
    <header class="discography-header">
      <SpotifySectionTitle :title="t('spotify.discography')" linked @open="$emit('show-all', current.id)" />
      <ButtonGroup v-if="groups.length > 1" :model-value="current.id" :options="options"
        size="small" inactive-variant="surface" width="hug" mobile-layout="scroll"
        @update:model-value="$emit('update:group', $event)" />
    </header>
    <SpotifyShelfRow :key="current.id" :items="cards" @select="$emit('select', $event)" />
  </section>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import SpotifyShelfRow from './SpotifyShelfRow.vue';
import SpotifySectionTitle from './SpotifySectionTitle.vue';
import { useSpotifyDiscography } from '@/composables/useSpotifyDiscography';

const props = defineProps({
  // The discography's lists as /api/spotify/artists files them: { id, title, items }.
  groups: {
    type: Array,
    required: true,
  },
  // The id of the list shown; the first one when it is not (or no longer) there.
  group: {
    type: String,
    default: '',
  },
});

// `show-all`: the list shown, to open whole; `select`: a release to open.
defineEmits(['select', 'show-all', 'update:group']);

const { t } = useI18n();

const { current, options, cards } = useSpotifyDiscography(() => props.groups, () => props.group);
</script>

<style scoped>
.discography {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.discography-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

/* The phone has no room beside the title: the lists go under it, sideways. */
@media (max-aspect-ratio: 4/3) {
  .discography-header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
