<template>
  <div class="audio-source-layout" ref="layoutRef">
    <!-- Content area: scrollable views -->
    <div
      class="content-container"
      :class="{ 'with-player': showPlayer }"
    >
      <!-- Header centralisé -->
      <ModalHeader
        :title="headerTitle"
        :subtitle="headerSubtitle"
        :show-back="headerShowBack"
        :variant="headerVariant"
        :icon="headerIcon"
        :actions-key="headerActionsKey"
        @back="$emit('header-back')"
      >
        <template #actions="slotProps">
          <slot name="header-actions" v-bind="slotProps" />
        </template>
      </ModalHeader>

      <!-- Contenu avec animation (wrapper pour isoler position: absolute) -->
      <div class="transition-wrapper">
        <Transition name="fade-slide" mode="out-in" @after-enter="resetScroll">
          <div :key="contentKey" class="content-inner">
            <slot name="content" />
          </div>
        </Transition>
      </div>
    </div>

    <!-- Player wrapper: animates width on desktop, transparent on mobile -->
    <div
      :class="['player-wrapper', { 'has-player': showPlayer }]"
    >
      <slot name="player" :player-width="playerWidth"></slot>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import ModalHeader from '@/components/ui/ModalHeader.vue'

const layoutRef = ref(null)

function resetScroll() {
  if (layoutRef.value) {
    layoutRef.value.scrollTop = 0
  }
}

const props = defineProps({
  /**
   * Controls layout animation (shows/hides player space)
   */
  showPlayer: {
    type: Boolean,
    default: false
  },
  /**
   * Header title
   */
  headerTitle: {
    type: String,
    default: ''
  },
  /**
   * Header subtitle (optional)
   */
  headerSubtitle: {
    type: String,
    default: null
  },
  /**
   * Show back button in header
   */
  headerShowBack: {
    type: Boolean,
    default: false
  },
  /**
   * Header variant ('contrast' or 'background-neutral')
   */
  headerVariant: {
    type: String,
    default: 'background-neutral'
  },
  /**
   * Header icon
   */
  headerIcon: {
    type: String,
    default: null
  },
  /**
   * Key for header actions transition
   */
  headerActionsKey: {
    type: String,
    default: 'default'
  },
  /**
   * Key for content transition (triggers fade-slide on change)
   */
  contentKey: {
    type: String,
    default: 'default'
  }
})

defineEmits(['header-back'])

// Player width for desktop (310px wrapper - 32px padding)
const playerWidth = 278
</script>

<style scoped>
/* Layout wrapper */
.audio-source-layout {
  --audio-player-wrapper-width: 310px;
  display: flex;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0 var(--space-07);
  transition: all var(--transition-spring-slow);
  overflow-y: auto;
}

/* Content container: animates width to make space for player */
.content-container {
  position: relative;
  width: 84%;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: var(--space-07) 0;
  gap: var(--space-06);
  min-height: 0;
  flex-shrink: 0;
  touch-action: pan-y;
  transition: width 0.6s cubic-bezier(0.5, 0, 0, 1);
}

.content-container.with-player {
  width: calc(100% - var(--audio-player-wrapper-width));
  transition: width var(--transition-spring);
}

/* Transition wrapper: isolates position: absolute during fade-slide */
.transition-wrapper {
  position: relative;
  flex: 1;
  min-height: 0;
}

/* Inner wrapper for content transition */
.content-inner {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  width: 100%;
}

/* Player wrapper: animates width to create space for player */
.player-wrapper {
  box-sizing: border-box;
  width: 0;
  height: 100%;
  max-width: var(--audio-player-wrapper-width);
  padding-left: 0;
  padding-top: var(--space-07);
  padding-bottom: var(--space-07);
  opacity: 0;
  flex-shrink: 0;
  position: sticky;
  top: 0;
  transition:
    width 0.6s cubic-bezier(0.5, 0, 0, 1),
    padding-left 0.6s cubic-bezier(0.5, 0, 0, 1),
    opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  pointer-events: none;
}

.player-wrapper.has-player {
  width: var(--audio-player-wrapper-width);
  max-width: var(--audio-player-wrapper-width);
  padding-left: var(--space-06); /* 32px spacing (animated with width) */
  opacity: 1;
  transition:
    width var(--transition-spring),
    padding-left var(--transition-spring),
    opacity 0.4s ease-out;
  pointer-events: all;
}

/* Mobile: full width content + fixed player (wrapper transparent) */
@media (max-aspect-ratio: 4/3) {
  .audio-source-layout {
    padding: 0 var(--space-05);
  }

  .content-container {
    width: 100%;
    max-width: none;
    height: auto;
    min-height: 100%;
    padding-bottom: var(--space-04);
    padding-top: var(--space-09);
  }

  .content-container.with-player {
    width: 100%;
    margin-right: 0;
  }

  .player-wrapper {
    display: contents;
    width: auto;
    opacity: 1;
  }

  .player-wrapper.has-player {
    width: auto;
    opacity: 1;
  }
}
</style>
