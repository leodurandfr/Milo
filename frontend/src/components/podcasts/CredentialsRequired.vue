<template>
  <div class="credentials-required">
    <!-- Credentials error: missing or invalid -->
    <MessageContent
      v-if="showCredentialsError"
      icon="podcast"
      :title="t('podcasts.credentialsError')"
      :subtitle="t('podcasts.credentialsErrorHint')"
      :cta-label="t('podcasts.configureButton')"
      :cta-click="() => $emit('configure')"
    />

    <!-- Rate limit error -->
    <MessageContent
      v-else-if="showRateLimitError"
      icon="podcast"
      :title="t('podcasts.rateLimitError')"
      :subtitle="t('podcasts.rateLimitErrorHint')"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSettingsStore } from '@/stores/settingsStore'
import { useI18n } from '@/services/i18n'
import MessageContent from '@/components/ui/MessageContent.vue'

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

</style>
