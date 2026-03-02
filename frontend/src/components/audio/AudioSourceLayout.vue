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
        @back="$emit('header-back')"
      >
        <template #actions="slotProps">
          <slot name="header-actions" v-bind="slotProps" />
        </template>
      </ModalHeader>

      <!-- Content with crossfade animation (wrapper isolates position: absolute during leave) -->
      <div class="transition-wrapper" ref="transitionWrapperRef">
        <Transition name="content-switch" appear @before-leave="onBeforeLeave" @after-enter="onAfterEnter">
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
import { ref, computed } from 'vue'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import { useIsMobile } from '@/composables/useIsMobile'

const layoutRef = ref(null)
const transitionWrapperRef = ref(null)

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
   * Key for content transition (triggers crossfade on change)
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

function onBeforeLeave(el) {
  // Pin wrapper height to prevent collapse while leaving element is position:absolute
  if (transitionWrapperRef.value) {
    transitionWrapperRef.value.style.minHeight = `${el.offsetHeight}px`
  }
  resetScroll()
}

function onAfterEnter() {
  if (transitionWrapperRef.value) {
    transitionWrapperRef.value.style.minHeight = ''
  }
}

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

/* Transition wrapper: isolates position: absolute during leave */
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

/* Content switch: crossfade with no blank gap */
.content-switch-enter-active {
  transition: all var(--transition-in-out);
}

.content-switch-leave-active {
  position: absolute;
  width: 100%;
  transition: opacity var(--transition-fast);
}

.content-switch-enter-from {
  opacity: 0;
  transform: translateY(var(--space-05));
}

.content-switch-leave-to {
  opacity: 0;
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
    padding-top: calc(max(24px, env(safe-area-inset-top, 0px)) + 8px);
    padding-bottom: var(--space-08);
  }

  .content-container.has-player {
    width: 100%;
    margin-right: 0;
    padding-bottom: calc(v-bind(mobilePlayerPadding) + env(safe-area-inset-bottom, 0px));
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

:deep(.modal-header) {
  transition: padding var(--transition-fast);
}
</style>
