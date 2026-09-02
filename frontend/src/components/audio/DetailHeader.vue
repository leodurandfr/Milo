<template>
  <div class="detail-header">
    <div v-if="icon" class="detail-header-cover detail-header-cover--icon">
      <SvgIcon :name="icon" :size="48" />
    </div>
    <LazyImage
      v-else
      :src="imageSrc"
      :fallback="fallback"
      :alt="title"
      priority="high"
      class="detail-header-cover"
    />

    <div class="detail-header-meta">
      <div class="detail-header-titles">
        <h2 class="detail-header-title heading-2">{{ title }}</h2>
        <p v-if="subtitle" class="detail-header-subtitle heading-3" :class="{ 'detail-header-subtitle--clickable': subtitleClickable }"
          @click="subtitleClickable && $emit('select-artist')">{{ subtitle }}</p>
        <p v-if="subtitleMeta" class="detail-header-metaline text-mono-medium">{{ subtitleMeta }}</p>
      </div>

      <div v-if="hasActions" class="detail-header-actions">
        <!-- Extra actions (e.g. the playlist Edit/Done toggle, or podcast Subscribe/Unsubscribe). -->
        <slot name="actions"></slot>
        <IconButton
          v-if="showFavorite"
          :icon="isFavorite ? 'heart' : 'heartOff'"
          variant="on-dark"
          size="small"
          @click="$emit('toggle-favorite')"
        />
        <IconButton v-if="showShuffle" icon="shuffle" variant="on-dark" size="small"
          :aria-label="t('musicLibrary.shuffle')" @click="$emit('shuffle')" />
        <IconButton v-if="showPlay" icon="play" variant="brand" size="medium"
          :aria-label="t('musicLibrary.play')" @click="$emit('play')" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, useSlots } from 'vue';
import { useI18n } from '@/services/i18n';
import LazyImage from '@/components/ui/LazyImage.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import IconButton from '@/components/ui/IconButton.vue';

const props = defineProps({
  imageSrc: {
    type: String,
    default: '',
  },
  fallback: {
    type: String,
    default: '',
  },
  // Icon name → tinted icon tile instead of cover art (virtual headers).
  icon: {
    type: String,
    default: '',
  },
  title: {
    type: String,
    required: true,
  },
  subtitle: {
    type: String,
    default: '',
  },
  subtitleMeta: {
    type: String,
    default: '',
  },
  subtitleClickable: {
    type: Boolean,
    default: false,
  },
  showPlay: {
    type: Boolean,
    default: true,
  },
  showShuffle: {
    type: Boolean,
    default: true,
  },
  showFavorite: {
    type: Boolean,
    default: false,
  },
  isFavorite: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['play', 'shuffle', 'toggle-favorite', 'select-artist']);

const { t } = useI18n();
const slots = useSlots();

const hasActions = computed(
  () => props.showPlay || props.showShuffle || props.showFavorite || !!slots.actions
);
</script>

<style scoped>
.detail-header {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
}

.detail-header-cover {
  flex-shrink: 0;
  width: 150px;
  height: 150px;
  border-radius: var(--radius-02);
}

.detail-header-cover--icon {
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-neutral-12);
  color: var(--color-brand);
}

.detail-header-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  gap: var(--space-04);
}

.detail-header-titles {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  min-width: 0;
}

.detail-header-title {
  margin: 0;
  color: var(--color-text-contrast);
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.detail-header-subtitle {
  margin: 0;
  width: fit-content;
  max-width: 100%;
  color: var(--color-brand);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-header-subtitle--clickable {
  cursor: pointer;
}

.detail-header-metaline {
  margin: 0;
  color: var(--color-text-contrast-50);
}

.detail-header-actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  flex-shrink: 0;
}

@media (max-aspect-ratio: 4/3) {
  .detail-header {
    flex-direction: column;
    align-items: stretch;
  }

  .detail-header-cover {
    width: 100%;
    height: auto;
    aspect-ratio: 1 / 1;
  }
}
</style>
