<!--
  DetailHeader's placeholder, for a page whose header is still being fetched.
  It keeps the header's box and typography (each bar sits in a line of the text
  class it stands for), so the page below does not move when the header arrives.
-->
<template>
  <div class="skeleton-detail-header" aria-hidden="true">
    <div class="skeleton-detail-header__cover shimmer shimmer--on-contrast"></div>

    <div class="skeleton-detail-header__meta">
      <div class="skeleton-detail-header__titles">
        <span class="skeleton-detail-header__line" :class="isMobile ? 'heading-4' : 'heading-2'">
          <span class="skeleton-text-line shimmer shimmer--on-contrast skeleton-detail-header__title"></span>&#8203;
        </span>
        <span v-if="subtitle" class="skeleton-detail-header__line" :class="isMobile ? 'heading-5' : 'heading-3'">
          <span class="skeleton-text-line shimmer shimmer--on-contrast skeleton-detail-header__subtitle"></span>&#8203;
        </span>
        <span class="skeleton-detail-header__line text-mono-medium">
          <span class="skeleton-text-line shimmer shimmer--on-contrast skeleton-detail-header__metaline"></span>&#8203;
        </span>
      </div>

      <div class="skeleton-detail-header__actions">
        <div v-if="shuffle" class="skeleton-detail-header__shuffle shimmer shimmer--on-contrast"></div>
        <div class="shimmer shimmer--on-contrast" :class="`skeleton-detail-header__${action}`"></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useIsMobile } from '@/composables/useIsMobile';

defineProps({
  // The subtitle line (an album's artists), which most headers do not draw.
  subtitle: {
    type: Boolean,
    default: false,
  },
  // The header's shuffle button, beside its main action.
  shuffle: {
    type: Boolean,
    default: false,
  },
  // The main action's shape: the labelled Play, a medium IconButton (an
  // episode's play), or a small labelled Button (a show's Subscribe).
  action: {
    type: String,
    default: 'play',
    validator: (value) => ['play', 'icon', 'small'].includes(value),
  },
});

const { isMobile } = useIsMobile();
</script>

<style scoped>
/* DetailHeader's box: change one, change both. */
.skeleton-detail-header {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  background: var(--color-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
}

.skeleton-detail-header__cover {
  flex-shrink: 0;
  width: 150px;
  height: 150px;
  border-radius: var(--radius-02);
}

.skeleton-detail-header__meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  gap: var(--space-04);
}

.skeleton-detail-header__titles {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  min-width: 0;
}

.skeleton-detail-header__line {
  display: flex;
  align-items: center;
  width: 100%;
}

.skeleton-detail-header__title {
  width: 60%;
  height: 24px;
}

.skeleton-detail-header__subtitle {
  width: 40%;
}

.skeleton-detail-header__metaline {
  width: 25%;
  height: 12px;
}

.skeleton-detail-header__actions {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  flex-shrink: 0;
}

/* A medium IconButton, a labelled medium Button (an IconButton on the phone),
   a labelled small Button. */
.skeleton-detail-header__shuffle,
.skeleton-detail-header__icon,
.skeleton-detail-header__play {
  height: 48px;
  border-radius: var(--radius-04);
}

.skeleton-detail-header__shuffle,
.skeleton-detail-header__icon {
  width: 48px;
}

.skeleton-detail-header__small {
  width: 96px;
  height: 36px;
  border-radius: var(--radius-03);
}

.skeleton-detail-header__play {
  width: 104px;
}

@media (max-aspect-ratio: 4/3) {
  .skeleton-detail-header__cover {
    width: 64px;
    height: 64px;
    border-radius: var(--radius-01);
  }

  .skeleton-detail-header__meta {
    align-items: center;
  }

  .skeleton-detail-header__titles {
    gap: var(--space-01);
  }

  .skeleton-detail-header__title {
    height: 14px;
  }

  .skeleton-detail-header__shuffle,
  .skeleton-detail-header__icon,
  .skeleton-detail-header__play {
    height: 38px;
    border-radius: var(--radius-03);
  }

  .skeleton-detail-header__icon {
    width: 38px;
  }

  .skeleton-detail-header__small {
    height: 34px;
  }

  .skeleton-detail-header__shuffle {
    width: 38px;
  }

  .skeleton-detail-header__play {
    width: 38px;
  }
}
</style>
