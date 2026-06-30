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
      <NavigationHeader
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
      </NavigationHeader>

      <!-- Content with crossfade animation (wrapper isolates position: absolute during leave) -->
      <div class="transition-wrapper">
        <Transition name="fade-slide" appear @before-leave="onBeforeLeave" @enter="onEnter" @after-leave="onAfterLeave">
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
      <slot name="player" :is-mobile="isMobile"></slot>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onBeforeUpdate } from 'vue'
import NavigationHeader from '@/components/ui/NavigationHeader.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { useViewTransition } from '@/composables/useViewTransition'

const layoutRef = ref(null)

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
  },
  /**
   * Scroll position to restore after the entering transition completes.
   * Provided by the parent when navigating back to a previously scrolled view.
   * Null means forward navigation — scroll resets to 0 after the enter animation.
   */
  pendingScrollRestore: {
    type: Number,
    default: null
  }
})

const emit = defineEmits(['header-back', 'scroll-restored'])

// Scroll-aware view transition (shared with SettingsModal via composable)
const pendingScrollRef = computed(() => props.pendingScrollRestore)
const { prepareNavigation, onBeforeLeave: baseOnBeforeLeave, onEnter, onAfterLeave: baseOnAfterLeave } = useViewTransition({
  scrollElRef: layoutRef,
  pendingScrollRestore: pendingScrollRef,
  onScrollRestored: () => emit('scroll-restored'),
})

// Gradient fade on navigation when scroll position crosses the visibility boundary
let gradientNeedsFadeIn = false
let gradientNeedsFadeOut = false

function onBeforeLeave(el) {
  const isForwardNav = pendingScrollRef.value === null
  const targetScroll = pendingScrollRef.value ?? 0
  const scrollEl = layoutRef.value
  const currentScroll = scrollEl?.scrollTop || 0

  // Forward nav from scrolled position → fade gradient in after scroll reset
  gradientNeedsFadeIn = isForwardNav && !!props.gradient && currentScroll > 16

  // Back nav to scrolled position while gradient is visible → fade out during transition
  gradientNeedsFadeOut = !isForwardNav && !!props.gradient && currentScroll <= 16 && targetScroll > 16

  if (gradientNeedsFadeOut) {
    const gradientEl = layoutRef.value?.querySelector('.background-gradient')
    if (gradientEl) {
      gradientEl.style.opacity = '0'
    }
  }

  baseOnBeforeLeave(el)
}

function onAfterLeave() {
  baseOnAfterLeave()

  if (gradientNeedsFadeIn) {
    const gradientEl = layoutRef.value?.querySelector('.background-gradient')
    if (gradientEl) {
      gradientEl.style.opacity = '0'
      gradientEl.style.transition = 'none'
      gradientEl.offsetHeight
      gradientEl.style.transition = ''
      gradientEl.style.opacity = ''
    }
    gradientNeedsFadeIn = false
  }

  if (gradientNeedsFadeOut) {
    // Gradient already faded out, scroll restored — reset inline styles
    // (gradient is scrolled out of view, so instant reset is invisible)
    const gradientEl = layoutRef.value?.querySelector('.background-gradient')
    if (gradientEl) {
      gradientEl.style.transition = 'none'
      gradientEl.style.opacity = ''
      gradientEl.offsetHeight
      gradientEl.style.transition = ''
    }
    gradientNeedsFadeOut = false
  }
}

// Auto-detect navigation: onBeforeUpdate fires after props have new values
// but BEFORE Vue patches the DOM, so prepareNavigation runs ahead of the cross-fade.
let prevContentKey = props.contentKey
onBeforeUpdate(() => {
  if (props.contentKey !== prevContentKey) {
    prepareNavigation()
    prevContentKey = props.contentKey
  }
})

// Mobile detection for padding-bottom
const { isMobile } = useIsMobile()

// Computed padding for mobile player
const mobilePlayerPadding = computed(() => `${props.playerMobileHeight}px`)
</script>

<style scoped>
/* Layout wrapper */
.audio-source-layout {
  --audio-player-wrapper-width: 360px;
  position: relative;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  width: 100%;
  height: 100%;
  padding: 0 var(--space-07);
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
  transition: opacity 400ms ease-out;
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

/* View stack: leaving + entering views share one grid cell, so the box reserves
   max(leaving, entering) height intrinsically (no position:absolute overlay). */
.transition-wrapper {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  min-height: 0;
}

/* Inner wrapper for content transition */
.content-inner {
  display: flex;
  flex-direction: column;
  min-height: 0;
  width: 100%;
}

/* Both views occupy the single stack cell during the cross-fade. align-self:start
   keeps each at its natural height. */
:deep(.fade-slide-enter-active),
:deep(.fade-slide-leave-active) {
  grid-row: 1;
  grid-column: 1;
  align-self: start;
}

/* Enter starts after leave begins (sequential fade-out → fade-in) */
:deep(.fade-slide-enter-active) {
  transition-delay: 100ms;
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

:deep(.navigation-header) {
  position: relative;
  z-index: 2;
  transition: padding var(--transition-fast), opacity var(--transition-in-out);
}
</style>
