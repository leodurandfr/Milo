<template>
  <div class="audio-source-frame">
    <!-- Background gradient (Radio/Podcast only). It hangs from the frame
         rather than from the scroller below, because a touch rubber-band
         translates every layer inside the scroll flow and WebKit paints nothing
         above the content origin — a gradient living in there just detaches
         from the top edge on drag-down. syncGradientToScroll() gives it back
         the scroll offset it loses by sitting out here. -->
    <div v-if="gradient" class="gradient-clip">
      <div
        ref="gradientRef"
        class="background-gradient"
        :class="`gradient-${gradient}`"
      />
    </div>

    <div class="audio-source-layout" ref="layoutRef">
      <!-- Content area: scrollable views. source-motion: what the source swap
           rises, together with .player-wrapper below — .audio-source-layout is
           the scroll clip and .background-gradient hangs off the frame, so those
           two stay welded to the screen edges instead. -->
      <div
        class="content-container source-motion"
        :class="{ 'has-player': showPlayer, 'screensaver-revealing': revealing }"
      >
        <!-- Back-to-top threshold marker. Absolute so it takes no row in the flex
             column (a zero-height item would still claim the container's gap), and
             outside .transition-wrapper so a view swap never re-creates it. -->
        <div ref="scrollSentinel" class="scroll-top-sentinel"></div>

        <NavigationHeader
          ref="headerRef"
          :title="headerTitle"
          :subtitle="headerSubtitle"
          :show-back="headerShowBack"
          :variant="headerVariant"
          :icon="headerIcon"
          :actions-key="headerActionsKey"
          :title-muted="headerTitleMuted"
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

      <!-- Player wrapper: animates width on desktop, transparent on mobile.
           Marked source-motion so the sticky pane leaves with the column beside it
           rather than standing still while it rises. Inert on mobile, where the
           wrapper is display: contents and the player is teleported to <body>. -->
      <div
        :class="['player-wrapper source-motion', { 'has-player': showPlayer }]"
      >
        <slot name="player" :is-mobile="isMobile"></slot>
      </div>

      <!-- Back to top. The anchor owns the horizontal position (it tracks the
           content column as the player opens); the button owns the entrance. -->
      <div :class="['scroll-top-anchor', { 'has-player': showPlayer }]">
        <Transition name="scroll-top">
          <IconButton
            v-if="scrollTopVisible"
            class="scroll-top-button"
            icon="caretUp"
            variant="rounded"
            size="large"
            :aria-label="t('common.backToTop')"
            @click="scrollToTop"
          />
        </Transition>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onBeforeUpdate, onMounted, onBeforeUnmount } from 'vue'
import NavigationHeader from '@/components/ui/NavigationHeader.vue'
import IconButton from '@/components/ui/IconButton.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { useViewTransition } from '@/composables/useViewTransition'
import { useScrollToTop } from '@/composables/useScrollToTop'
import { useScreensaverRevealPulse } from '@/composables/useScreensaverReveal'
import { useI18n } from '@/services/i18n'

const { t } = useI18n()

const layoutRef = ref(null)
const headerRef = ref(null)
const gradientRef = ref(null)

// The scroll container, for the callers that save and restore a scroll position
// across navigation. Exposed by name because the root element is the frame, not
// the scroller: $el would hand them a box whose scrollTop is always 0, and a
// restore that silently lands at the top is how music-library's lost its own.
defineExpose({
  get scrollElement() {
    return layoutRef.value
  },
})

const props = defineProps({
  /**
   * Controls layout animation (shows/hides player space)
   */
  showPlayer: {
    type: Boolean,
    default: false
  },
  /**
   * Background gradient variant — the audio source id, so it reads the same as
   * headerIcon on the same call site. Its own tint token, per source.
   */
  gradient: {
    type: String,
    default: null,
    validator: (value) => [null, 'radio', 'podcast', 'music_library'].includes(value)
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
   * Header variant, forwarded to NavigationHeader — same accepted set as its
   * own `variant`, restated here so a bad value is caught at this call site
   * rather than one component deeper.
   */
  headerVariant: {
    type: String,
    default: 'background-neutral',
    validator: (value) => ['contrast', 'background-neutral'].includes(value)
  },
  /**
   * Render the header title in the secondary (muted) text color
   */
  headerTitleMuted: {
    type: Boolean,
    default: false
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
  onScrollRestored: () => {
    syncGradientToScroll()
    emit('scroll-restored')
  },
  headerRef,
})

// The gradient hangs off the frame, so the scroll offset it used to inherit is
// applied here. Clamped at zero on purpose: a touch rubber-band drives scrollTop
// negative, and refusing that one direction of travel is what welds the gradient
// to the top edge while the content bounces away from it. CSS cannot do it —
// measured on iOS, WebKit paints nothing above the content origin during a
// bounce, so a layer inside the scroll flow detaches whatever is drawn behind it.
function syncGradientToScroll() {
  const el = gradientRef.value
  if (!el) return
  const offset = Math.max(0, layoutRef.value?.scrollTop || 0)
  el.style.transform = `translate3d(0, ${-offset}px, 0)`
}

onMounted(() => {
  layoutRef.value?.addEventListener('scroll', syncGradientToScroll, { passive: true })
  syncGradientToScroll()
})

onBeforeUnmount(() => {
  layoutRef.value?.removeEventListener('scroll', syncGradientToScroll)
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
    const gradientEl = gradientRef.value
    if (gradientEl) {
      gradientEl.style.opacity = '0'
    }
  }

  baseOnBeforeLeave(el)
}

function onAfterLeave() {
  baseOnAfterLeave()

  if (gradientNeedsFadeIn) {
    const gradientEl = gradientRef.value
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
    const gradientEl = gradientRef.value
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

// Back to top: shown once two screenfuls have been scrolled past, which is
// exactly the point where flicking back up stops being reasonable. Every view
// hosted here gets it, so no source view opts in.
const { sentinelRef: scrollSentinel, isVisible: scrollTopVisible, scrollToTop } = useScrollToTop(layoutRef)

// Mobile detection for padding-bottom
const { isMobile } = useIsMobile()

// Replay the content entrance when the screensaver is dismissed.
const revealing = useScreensaverRevealPulse()

// Computed padding for mobile player
const mobilePlayerPadding = computed(() => `${props.playerMobileHeight}px`)
</script>

<style scoped>
/* The non-scrolling frame. Same box as the scroller it holds, and the only
   reason it exists: it gives .background-gradient a parent a bounce cannot
   move. It must not clip — an `overflow` here would make it a scroll container,
   and the scroller's overscroll would chain into it instead of rubber-banding. */
.audio-source-frame {
  position: relative;
  width: 100%;
  height: 100%;
}

/* Holds the gradient's travel once the view is scrolled. A sibling of the
   scroller rather than its parent, for the reason just above: this one clips,
   and sits outside the scroll chain where that costs nothing. */
.gradient-clip {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

/* Layout wrapper */
.audio-source-layout {
  --audio-player-wrapper-width: 340px;
  position: relative;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  width: 100%;
  height: 100%;
  padding: 0 var(--space-07);
  overflow-y: auto;
  /* Hide the scrollbar (Firefox) so a scrollbar appearing/disappearing never
     reflows the content-box width — otherwise .content-container's percentage
     width resolves differently and its width transition animates the shift. */
  scrollbar-width: none;
}

/* Same, WebKit — keeps content width independent of scrollbar presence. */
.audio-source-layout::-webkit-scrollbar {
  display: none;
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
  background: var(--gradient-source-radio);
}

.gradient-podcast {
  background: var(--gradient-source-podcast);
}

.gradient-music_library {
  background: var(--gradient-source-music-library);
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
  /* The transform half is the source swap's rise opting in beside this
     element's own width animation — `transition` is a shorthand, so the shared
     rule cannot add to it from outside. See .source-motion in
     design-system.css, which owns the duration. */
  transition:
    width 0.6s cubic-bezier(0.5, 0, 0, 1),
    transform var(--source-motion-duration, 0s);
  /* The source swap rises this element (see .source-motion in
     design-system.css), and its scale has to shrink towards the top rather than
     the default centre. This column is top-anchored and taller than the screen,
     so a centred origin pushes the top back down by half the overshoot while the
     translate pulls it up — the header rose 19px of the 32 it was given, and the
     leave read as a zoom-out about the middle instead of a departure upwards.
     AudioPlayerFull needs no such thing: its moving element is exactly the
     viewport, so its centre and the screen's are the same point. */
  transform-origin: 50% 0;
}

.content-container.has-player {
  width: calc(100% - var(--audio-player-wrapper-width));
  transition:
    width var(--transition-spring),
    transform var(--source-motion-duration, 0s);
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
  /* transform: the source swap's rise, opting in beside this element's own
     animations — see .content-container above for why it has to be declared
     here rather than by the shared rule. */
  transition:
    width 0.6s cubic-bezier(0.5, 0, 0, 1),
    padding-left 0.6s cubic-bezier(0.5, 0, 0, 1),
    opacity 0.6s cubic-bezier(0.5, 0, 0, 1),
    transform var(--source-motion-duration, 0s);
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
    opacity 0.4s ease-out,
    transform var(--source-motion-duration, 0s);
  pointer-events: all;
}

/* Back-to-top threshold marker — geometry only, never painted. */
.scroll-top-sentinel {
  position: absolute;
  top: 0;
  left: 0;
  width: 1px;
  height: 1px;
  pointer-events: none;
}

/* Anchor: fixed, so it neither scrolls nor takes a slot in the layout's flex
   row. It sits where the NavigationHeader rests, which is what the button
   stands in for once the header has scrolled away. The X offset tracks the
   content column on the same two curves .content-container uses for its width,
   so pill and content move together when the player opens or closes. */
.scroll-top-anchor {
  position: fixed;
  top: var(--space-07);
  left: 50%;
  z-index: 3;
  transform: translateX(-50%);
  transition: transform 0.6s cubic-bezier(0.5, 0, 0, 1);
  pointer-events: none;
}

.scroll-top-anchor.has-player {
  transform: translateX(calc(-50% - var(--audio-player-wrapper-width) / 2));
  transition: transform var(--transition-spring);
}

.scroll-top-button {
  pointer-events: auto;
}

/* Same entrance as Modal's close button: a snappy spring down from -24px with
   the opacity fading in on its own ease-out curve. The exit is that entrance
   reversed on the standard leave timing — Modal has no exit of its own there,
   its overlay takes the button with it. */
.scroll-top-enter-active {
  transition:
    transform var(--transition-spring-snappy),
    opacity 350ms var(--easeOutCubic);
}

.scroll-top-leave-active {
  transition:
    transform var(--transition-fast-leave),
    opacity var(--transition-fast-leave);
}

.scroll-top-enter-from,
.scroll-top-leave-to {
  opacity: 0;
  transform: translateY(-24px);
}

/* Mobile: full width content + fixed player (wrapper transparent) */
@media (max-aspect-ratio: 4/3) {
  .audio-source-layout {
    padding: 0 var(--space-05);
  }

  .content-container {
    width: 100%;
    max-width: none;
    padding-top: calc(max(var(--space-05-fixed), env(safe-area-inset-top, 0px)) + var(--space-02));
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

  /* The content is full width here (the player is a fixed bottom bar), so there
     is no column to offset against — the pill stays centred in both states. */
  .scroll-top-anchor,
  .scroll-top-anchor.has-player {
    top: calc(max(var(--space-05-fixed), env(safe-area-inset-top, 0px)) + var(--space-02));
    transform: translateX(-50%);
  }
}

:deep(.navigation-header) {
  position: relative;
  z-index: 2;
  transition: padding var(--transition-fast), opacity var(--transition-in-out);
}
</style>
