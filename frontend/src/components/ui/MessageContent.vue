<template>
  <div class="message-content"
    :class="{ 'is-delayed': loading && !showLoading, 'mc--no-glyph': !icon && !showLoading, 'message-content--on-contrast': variant === 'on-contrast' }">
    <!-- Loading spinner OR icon (mutually exclusive) — same size, so a card
         swapping one for the other doesn't resize its glyph mid-transition. -->
    <LoadingSpinner v-if="showLoading" :size="48" />
    <SvgIcon v-else-if="icon" :name="icon" :size="48" :color="iconColor" />

    <!-- Content always visible (even while loading) -->
    <p v-if="displayTitle" class="heading-2 mc-title">{{ displayTitle }}</p>
    <p v-if="subtitle" class="text-body-medium mc-subtitle" v-html="subtitle"></p>
    <p v-if="details" class="text-body-medium mc-details">{{ details }}</p>
    <div v-if="ctaLabel || ctaSecondaryLabel" class="cta-group">
      <Button v-if="ctaLabel" :variant="ctaVariant" :loading="ctaLoading" @click="ctaClick">
        {{ ctaLabel }}
      </Button>
      <Button v-if="ctaSecondaryLabel" :variant="ctaSecondaryVariant" @click="ctaSecondaryClick">
        {{ ctaSecondaryLabel }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useTimer } from '@/composables/useTimer'
import { useI18n } from '@/services/i18n'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
import Button from '@/components/ui/Button.vue'

const props = defineProps({
  // 'default' = a panel of wherever it is drawn; 'on-contrast' = card-less,
  // light-on-dark, for a state laid over a contrast surface (the Lyrics view's
  // blurred artwork).
  variant: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'on-contrast'].includes(value)
  },
  loading: {
    type: Boolean,
    default: false
  },
  loadingDelay: {
    type: Number,
    default: 200
  },
  icon: {
    type: String,
    default: null
  },
  title: {
    type: String,
    default: null
  },
  subtitle: {
    type: String,
    default: null
  },
  details: {
    type: String,
    default: null
  },
  ctaLabel: {
    type: String,
    default: null
  },
  ctaVariant: {
    type: String,
    default: 'brand'
  },
  ctaClick: {
    type: Function,
    default: null
  },
  ctaLoading: {
    type: Boolean,
    default: false
  },
  ctaSecondaryLabel: {
    type: String,
    default: null
  },
  ctaSecondaryVariant: {
    type: String,
    default: 'control'
  },
  ctaSecondaryClick: {
    type: Function,
    default: null
  }
})

const { t } = useI18n()

// A loading card always says so: a caller with nothing more specific to name
// gets the generic line, rather than a spinner alone.
const displayTitle = computed(() => props.title || (props.loading ? t('common.loading') : null))

const iconColor = computed(() =>
  props.variant === 'on-contrast' ? 'var(--color-text-on-contrast-secondary)' : 'var(--color-fill-soft)'
)

// Delayed loading state to avoid flash of spinner
const timer = useTimer()
const showLoading = ref(false)
let loadingTimeout = null

watch(() => props.loading, (isLoading) => {
  if (loadingTimeout) {
    timer.clear(loadingTimeout)
    loadingTimeout = null
  }

  if (isLoading) {
    if (props.loadingDelay > 0) {
      loadingTimeout = timer.setTimeout(() => {
        showLoading.value = true
      }, props.loadingDelay)
    } else {
      showLoading.value = true
    }
  } else {
    showLoading.value = false
  }
}, { immediate: true })
</script>

<style scoped>
.message-content {
  display: flex;
  min-height: 280px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  /* One gap for every block, owned by the container: it applies only BETWEEN
     children, so the rhythm no longer depends on which optional props a caller
     passes (a card with an icon spaced like one without). */
  gap: var(--space-04);
  padding: var(--space-07) var(--space-06) var(--space-08) var(--space-06);
  text-align: center;
  background: var(--color-surface);
  border-radius: var(--radius-06);
}

/* No leading icon/spinner: the reduced top padding exists to seat the glyph,
   so drop it and balance the card with symmetric vertical padding. */
.message-content.mc--no-glyph {
  padding-top: var(--space-08);
}

.message-content :deep(p),
.message-content :deep(.heading-2) {
  color: var(--color-text-secondary);
}

/* The spinner is bare — the light plate it used to carry belongs to an app-icon
   tile, not to a state card — so it takes the card's own colour, and the
   on-contrast variant has to name its own the way the icon beside it does. */
.message-content > :deep(.loading-spinner) {
  color: var(--color-text-secondary);
}

.message-content--on-contrast > :deep(.loading-spinner) {
  color: var(--color-text-on-contrast);
}

.cta-group {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-02);
  /* The one deliberate exception: an action needs more air than a line of copy,
     so it steps up on top of the container gap. Fixed, so it's the same step in
     every card that has a CTA. */
  margin-top: var(--space-02);
}


.message-content.is-delayed {
  visibility: hidden;
}

/* On-contrast variant — no card at all: the state floats over the contrast
   surface that hosts it, so the background, radius and card min-height all go,
   and only the inline padding stays to keep long copy off the screen edges. */
.message-content.message-content--on-contrast {
  min-height: 0;
  padding-block: 0;
  background: none;
  border-radius: 0;
}

/* Unlike the card, which colors every line alike, the on-contrast variant
   layers them: the copy sits over blurred artwork, so the title needs full
   contrast while the secondary lines fall back to stay out of its way. */
.message-content--on-contrast :deep(p),
.message-content--on-contrast :deep(.heading-2) {
  color: var(--color-text-on-contrast);
}

.message-content--on-contrast .mc-subtitle,
.message-content--on-contrast .mc-details {
  color: var(--color-text-on-contrast-secondary);
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }

  .message-content.message-content--on-contrast {
    min-height: 0;
  }
}
</style>
