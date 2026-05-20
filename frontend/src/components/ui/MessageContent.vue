<template>
  <div class="message-content" :class="{ 'is-delayed': loading && !showLoading }">
    <!-- Loading spinner OR icon (mutually exclusive) -->
    <LoadingSpinner v-if="showLoading" :size="64" />
    <SvgIcon v-else-if="icon" :name="icon" :size="64" color="var(--color-background-medium-16)" />

    <!-- Content always visible (even while loading) -->
    <p v-if="title" class="heading-2">{{ title }}</p>
    <p v-if="subtitle" class="text-mono" v-html="subtitle"></p>
    <p v-if="details" class="text-mono">{{ details }}</p>
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
import { ref, watch } from 'vue'
import { useTimer } from '@/composables/useTimer'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
import Button from '@/components/ui/Button.vue'

const props = defineProps({
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
  gap: var(--space-04);
  padding: var(--space-07) var(--space-06) var(--space-08) var(--space-06);
  text-align: center;
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
}

.message-content :deep(p),
.message-content :deep(.text-mono),
.message-content :deep(.heading-2) {
  color: var(--color-text-secondary);
}

.message-content :deep(.loading-spinner) {
  color: var(--color-text-secondary);
}

.message-content :deep(.btn) {
  margin-top: var(--space-02);
}

.cta-group {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-02);
}

.cta-group :deep(.btn) {
  margin-top: 0;
}


.message-content.is-delayed {
  visibility: hidden;
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}
</style>
