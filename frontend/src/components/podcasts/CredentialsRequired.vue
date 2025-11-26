<template>
  <div class="credentials-required">
    <!-- Credentials error: missing or invalid -->
    <div v-if="showCredentialsError" class="message-wrapper">
      <MessageContent>
        <SvgIcon name="podcast" :size="64" color="var(--color-background-medium-16)" />
        <p class="heading-2">{{ t('podcasts.credentialsError') }}</p>
        <p class="text-mono">{{ t('podcasts.credentialsErrorHint') }}</p>
        <Button variant="brand" @click="$emit('configure')">
          {{ t('podcasts.configureButton') }}
        </Button>
      </MessageContent>
    </div>

    <!-- Rate limit error -->
    <div v-else-if="showRateLimitError" class="message-wrapper">
      <MessageContent>
        <SvgIcon name="podcast" :size="64" color="var(--color-background-medium-16)" />
        <p class="heading-2">{{ t('podcasts.rateLimitError') }}</p>
        <p class="text-mono">{{ t('podcasts.rateLimitErrorHint') }}</p>
      </MessageContent>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settingsStore'
import { useI18n } from '@/services/i18n'
import Button from '@/components/ui/Button.vue'
import MessageContent from '@/components/ui/MessageContent.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'

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
</style>
