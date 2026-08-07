<template>
  <div class="message-content"
    :class="{ 'is-delayed': loading && !showLoading, 'mc--no-glyph': !icon && !showLoading, 'message-content--dark': variant === 'dark' }">
    <!-- Loading spinner OR icon (mutually exclusive) — same size, so a card
         swapping one for the other doesn't resize its glyph mid-transition. -->
    <LoadingSpinner v-if="showLoading" :size="48" />
    <SvgIcon v-else-if="icon" :name="icon" :size="48" :color="iconColor" />

    <!-- Content always visible (even while loading) -->
    <p v-if="title" class="heading-2 mc-title">{{ title }}</p>
    <p v-if="subtitle" class="text-body mc-subtitle" v-html="subtitle"></p>
    <p v-if="details" class="text-body mc-details">{{ details }}</p>
    <div v-if="ctaLabel || ctaSecondaryLabel" class="cta-group">
      <Button v-if="ctaLabel" :variant="ctaVariant" :loading="ctaLoading" @click="ctaClick">
        {{ ctaLabel }}
      </Button>
      <Button v-if="ctaSecondaryLabel" :variant="ctaSecondaryVariant" :loading="ctaSecondaryLoading" @click="ctaSecondaryClick">
        {{ ctaSecondaryLabel }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useTimer } from '@/composables/useTimer'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
import Button from '@/components/ui/Button.vue'

const props = defineProps({
  // 'default' = the white card; 'dark' = card-less, light-on-dark, for a state
  // laid over a dark backdrop (the Lyrics view's blurred artwork).
  variant: {
    type: String,
    default: 'default'
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
    default: 'background-strong'
  },
  ctaSecondaryClick: {
    type: Function,
    default: null
  },
  ctaSecondaryLoading: {
    type: Boolean,
    default: false
  }
})

const iconColor = computed(() =>
  props.variant === 'dark' ? 'var(--color-text-contrast-50)' : 'var(--color-background-medium-16)'
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
  background: var(--color-background-neutral);
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
   tile, not to a state card — so it takes the card's own colour, and the dark
   variant has to name its own the way the icon beside it does. */
.message-content > :deep(.loading-spinner) {
  color: var(--color-text-secondary);
}

.message-content--dark > :deep(.loading-spinner) {
  color: var(--color-text-contrast);
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

/* Dark variant — no card at all: the state floats over whatever dark surface
   hosts it, so the background, radius and card min-height all go, and only the
   inline padding stays to keep long copy off the screen edges. */
.message-content.message-content--dark {
  min-height: 0;
  padding-block: 0;
  background: none;
  border-radius: 0;
}

/* Unlike the light card, which colors every line alike, the dark variant layers
   them: the copy sits over blurred artwork, so the title needs full contrast
   while the secondary lines fall back to stay out of its way. */
.message-content--dark :deep(p),
.message-content--dark :deep(.heading-2) {
  color: var(--color-text-contrast);
}

.message-content--dark .mc-subtitle,
.message-content--dark .mc-details {
  color: var(--color-text-contrast-50);
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }

  .message-content.message-content--dark {
    min-height: 0;
  }
}
</style>
