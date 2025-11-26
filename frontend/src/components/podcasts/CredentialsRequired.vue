<template>
  <div class="credentials-required">
    <!-- Header sans icônes d'action -->
    <ModalHeader
      :title="t('podcasts.podcasts')"
      icon="podcast"
      variant="background-neutral"
      :showBack="false"
    />

    <!-- Credentials error: missing or invalid -->
    <div v-if="showCredentialsError" class="message-wrapper">
      <div class="message-content">
        <SvgIcon name="podcast" :size="96" color="var(--color-background-medium-16)" />
        <p class="heading-2">{{ t('podcasts.credentialsError') }}</p>
        <p class="text-mono">{{ t('podcasts.credentialsErrorHint') }}</p>
        <Button variant="brand" @click="$emit('configure')">
          {{ t('podcasts.configureButton') }}
        </Button>
      </div>
    </div>

    <!-- Rate limit error -->
    <div v-else-if="showRateLimitError" class="message-wrapper">
      <div class="message-content">
        <p class="heading-2">{{ t('podcasts.rateLimitError') }}</p>
        <p class="text-mono">{{ t('podcasts.rateLimitErrorHint') }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settingsStore'
import { useI18n } from '@/services/i18n'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import Button from '@/components/ui/Button.vue'

defineEmits(['configure'])

const { t } = useI18n()
const settingsStore = useSettingsStore()

const showCredentialsError = computed(() => {
  return settingsStore.podcastCredentialsStatus === 'missing' ||
         settingsStore.podcastCredentialsStatus === 'invalid'
})

const showRateLimitError = computed(() => {
  return settingsStore.podcastCredentialsStatus === 'rate_limited'
})
</script>

<style scoped>
.credentials-required {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
  width: 100%;
}

.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
  text-align: center;
}

.message-content .heading-2,
.message-content .text-mono {
  color: var(--color-text-secondary);
}

@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}
</style>
