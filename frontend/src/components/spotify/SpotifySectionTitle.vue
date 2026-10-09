<template>
  <!-- A section's title; with more than its row shows, the way to all of it
       (Spotify's "Show all"), marked by a caret. The caret is part of the
       text, glued to the last word: on a title that wraps it follows that
       word, never the widest line, and never sits alone on a line. A shelf
       about an artist is headed as the apps head it: the artist's photo, and
       what the title says around the name over the name itself. -->
  <div class="section-heading">
    <LazyImage v-if="image" :src="image" :fallback="musicPlaceholder" alt="" class="section-avatar" />
    <div class="section-heading-text">
      <p v-if="overline" class="section-overline text-body-small">{{ overline }}</p>
      <button v-if="linked" v-press type="button" class="section-title section-title--linked" @click="$emit('open')">
        <h2 class="heading-2">{{ head }}<span class="section-tail">{{ lastWord }}<SvgIcon name="caretRight"
          :size="24" class="section-caret" /></span></h2>
      </button>
      <h2 v-else class="section-title heading-2">{{ title }}</h2>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  title: {
    type: String,
    required: true,
  },
  linked: {
    type: Boolean,
    default: false,
  },
  // A shelf about an artist: the line over the title, and the artist's photo.
  overline: {
    type: String,
    default: '',
  },
  image: {
    type: String,
    default: '',
  },
});

defineEmits(['open']);

// The title up to and including its last space, then the word the caret holds on to.
const split = computed(() => props.title.trimEnd().lastIndexOf(' ') + 1);
const head = computed(() => props.title.trimEnd().slice(0, split.value));
const lastWord = computed(() => props.title.trimEnd().slice(split.value));
</script>

<style scoped>
.section-heading {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
}

.section-heading-text {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
}

.section-avatar {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: var(--radius-full);
  background: var(--color-surface-glass);
}

@media (max-aspect-ratio: 4/3) {
  .section-avatar {
    width: 40px;
    height: 40px;
  }
}

.section-overline {
  margin: 0;
  color: var(--color-text-secondary);
}

.section-title {
  color: var(--color-text);
  margin: 0;
}

.section-title--linked {
  display: block;
  align-self: flex-start;
  max-width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.section-title--linked h2 {
  margin: 0;
  color: inherit;
}

.section-tail {
  white-space: nowrap;
}

/* Centred on the title's lowercase rather than its capitals, which most of a
   title is: on the line's middle it read as riding high (2 px, measured). */
.section-caret {
  margin-left: var(--space-01);
  color: var(--color-text-tertiary);
  vertical-align: top;
  transform: translateY(2px);
}
</style>
