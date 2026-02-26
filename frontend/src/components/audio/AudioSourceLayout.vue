<template>
  <div class="audio-source-layout" ref="layoutRef">
    <!-- Background gradient (Radio/Podcast only) -->
    <div
      v-if="gradient"
      class="background-gradient"
      :class="`gradient-${gradient}`"
    />

    <!-- Content area: scrollable views -->
    <div
      class="content-container"
      :class="{ 'has-player': showPlayer }"
    >
      <!-- Header centralisé -->
      <ModalHeader
        :title="headerTitle"
        :subtitle="headerSubtitle"
        :show-back="headerShowBack"
        :variant="headerVariant"
        :icon="headerIcon"
        :actions-key="headerActionsKey"
        :class="{ 'header-hidden': headerHidden }"
        @back="$emit('header-back')"
      >
        <template #actions="slotProps">
          <slot name="header-actions" v-bind="slotProps" />
        </template>
      </ModalHeader>

      <!-- Contenu avec animation (wrapper pour isoler position: absolute) -->
      <div class="transition-wrapper">
        <Transition name="fade-slide" mode="out-in" appear @before-leave="onBeforeLeave" @after-leave="onAfterLeave" @enter="onEnter">
          <div :key="contentKey" class="content-inner">
            <slot name="content" :is-mobile="isMobile" />
          </div>
        </Transition>
      </div>
    </div>

    <!-- Player wrapper: animates width on desktop, transparent on mobile -->
    <div
      :class="['player-wrapper', { 'has-player': showPlayer }]"
    >
      <slot name="player" :player-width="playerWidth" :is-mobile="isMobile"></slot>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const layoutRef = ref(null)

// Header hidden state (only when scrolled)
const headerHidden = ref(false)
const wasScrolled = ref(false)

const props = defineProps({
  /**
   * Controls layout animation (shows/hides player space)
   */
  showPlayer: {
    type: Boolean,
    default: false
  },
  /**
   * Background gradient variant ('radio' or 'podcast')
   */
  gradient: {
    type: String,
    default: null,
    validator: (value) => [null, 'radio', 'podcast'].includes(value)
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
  },
  /**
   * Height of the mobile player (for padding-bottom)
   */
  playerMobileHeight: {
    type: Number,
    default: 144
  }
})

defineEmits(['header-back'])

// Transition hooks for header hide/show (only when scrolled)
function onBeforeLeave() {
  // Check if scrolled and hide header immediately so it fades out with content
  wasScrolled.value = layoutRef.value?.scrollTop > 0
  if (wasScrolled.value) {
    headerHidden.value = true
  }
}

function onAfterLeave() {
  // Reset scroll after content fade-out completes (header already hidden)
  resetScroll()
}

function onEnter() {
  // Double RAF to ensure browser paints hidden state before fade-in
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      headerHidden.value = false
    })
  })
}

// Reset scroll to top
function resetScroll() {
  if (layoutRef.value) {
    layoutRef.value.scrollTop = 0
  }
}

// Player width for desktop (310px wrapper - 32px padding)
const playerWidth = 278

// Mobile detection for padding-bottom
const { isMobile } = useIsMobile()

// Computed padding for mobile player
const mobilePlayerPadding = computed(() => `${props.playerMobileHeight}px`)
</script>

<style scoped>
/* Layout wrapper */
.audio-source-layout {
  --audio-player-wrapper-width: 310px;
  position: relative;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  width: 100%;
  height: 100%;
  padding: 0 var(--space-07);
  /* transition: all var(--transition-spring-slow); */
  overflow-y: auto;
}

/* Background gradient (Radio/Podcast) */
.background-gradient {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 66%;
  pointer-events: none;
  z-index: 0;
}

.gradient-radio {
  background: linear-gradient(180deg, #F6EDCD 0%, rgba(246, 237, 205, 0) 100%);
}

.gradient-podcast {
  background: linear-gradient(180deg, rgba(66, 24, 112, 0.08) 0%, rgba(126, 46, 214, 0) 100%);
}

/* Content container: animates width to make space for player */
.content-container {
  position: relative;
  z-index: 1;
  width: 84%;
  height: auto;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  padding: var(--space-07) 0;
  gap: var(--space-06);
  flex-shrink: 0;
  touch-action: pan-y;
  transition: width 0.6s cubic-bezier(0.5, 0, 0, 1);
}

.content-container.has-player {
  width: calc(100% - var(--audio-player-wrapper-width));
  transition: width var(--transition-spring);
}

/* Transition wrapper: isolates position: absolute during fade-slide */
.transition-wrapper {
  position: relative;
  min-height: 0;
}

/* Inner wrapper for content transition */
.content-inner {
  display: flex;
  flex-direction: column;
  min-height: 0;
  width: 100%;
}

/* Player wrapper: animates width to create space for player */
.player-wrapper {
  box-sizing: border-box;
  z-index: 1;
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
    padding-top: var(--space-09);
    padding-bottom: var(--space-08);

  }

  .content-container.has-player {
    width: 100%;
    margin-right: 0;
    padding-bottom: v-bind(mobilePlayerPadding);
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

/* Header hide/show transition (only when scroll reset is needed) */
:deep(.modal-header) {
  transition: opacity var(--transition-in-out); /* Fade-in: 400ms cubic-bezier(0.5,0,0.1,1) */
}

:deep(.modal-header.header-hidden) {
  opacity: 0;
  transition: opacity var(--transition-ultra-fast); /* Fade-out: 150ms ease */
}
</style>
