<!-- frontend/src/components/settings/categories/PodcastSettings.vue -->
<template>
  <SettingsSection :title="t('podcastSettings.taddyCredentials')">
    <SettingItem :label="t('podcastSettings.credentialsDescription')">
      <div class="credentials-form">
        <div class="form-field">
          <label class="form-label text-mono">{{ t('podcastSettings.userId') }}</label>
          <InputText
            v-model="localUserId"
            type="text"
            :placeholder="t('podcastSettings.userIdPlaceholder')"
          />
        </div>

        <div class="form-field">
          <label class="form-label text-mono">{{ t('podcastSettings.apiKey') }}</label>
          <InputText
            v-model="localApiKey"
            type="text"
            :placeholder="t('podcastSettings.apiKeyPlaceholder')"
          />
        </div>

        <div v-if="requestsUsed !== null" class="usage-display">
          <div class="usage-header">
            <label class="form-label text-mono">{{ t('podcastSettings.apiUsage') }}</label>
            <span class="usage-value text-mono">{{ requestsUsed }}/500 {{ t('podcastSettings.requestsPerMonth') }}</span>
          </div>
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: usagePercentage + '%' }"></div>
          </div>
          <span v-if="resetDateText" class="usage-description text-mono">{{ resetDateText }}</span>
          <span v-else class="usage-description text-mono">{{ t('podcastSettings.resetsMonthly') }}</span>
        </div>

        <!-- Test connection button - Visible when no credentials OR changes -->
        <div v-if="!hasCredentials || hasChanges" class="action-buttons-sticky">
          <Button
            variant="brand"
            :disabled="isValidating || !localUserId || !localApiKey"
            :loading="isValidating"
            :loading-label="false"
            @click="handleTestConnection"
          >
            {{ t('podcastSettings.validateButton') }}
          </Button>
        </div>

        <div v-if="errorMessage" class="status-message error text-mono">
          {{ errorMessage }}
        </div>
      </div>
    </SettingItem>
  </SettingsSection>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import { apiCall } from '@/services/apiCall';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const { t } = useI18n();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

const localUserId = ref('');
const localApiKey = ref('');
const isValidating = ref(false);
const errorMessage = ref('');

const requestsUsed = computed(() => settingsStore.podcastApiUsage);
const credentialsValidatedAt = computed(() => settingsStore.podcastCredentialsValidatedAt);

// Map Milo language codes to BCP 47 locale codes
const localeMap = {
  english: 'en',
  french: 'fr',
  german: 'de',
  spanish: 'es',
  italian: 'it',
  portuguese: 'pt',
  chinese: 'zh',
  hindi: 'hi'
};

// Calculate next API reset date (monthly cycle from validation date)
const resetDateText = computed(() => {
  if (!credentialsValidatedAt.value) return null;

  const validatedDate = new Date(credentialsValidatedAt.value * 1000);
  const resetDate = new Date(validatedDate);
  resetDate.setMonth(resetDate.getMonth() + 1);

  // If the reset date is in the past, find the next future reset date
  const now = new Date();
  while (resetDate <= now) {
    resetDate.setMonth(resetDate.getMonth() + 1);
  }

  const locale = localeMap[settingsStore.language] || 'en';
  const day = resetDate.getDate();
  const month = resetDate.toLocaleDateString(locale, { month: 'long' });

  return t('podcastSettings.resetsOn', { day, month });
});

// Reset error when fields change
watch([localUserId, localApiKey], () => {
  errorMessage.value = '';
});

// Load current credentials
const config = computed(() => ({
  taddy_user_id: settingsStore.podcastCredentials?.taddy_user_id || '',
  taddy_api_key: settingsStore.podcastCredentials?.taddy_api_key || ''
}));

// Calculate usage percentage (used requests out of 500)
const usagePercentage = computed(() => {
  if (requestsUsed.value === null) return 0;
  return Math.max(0, Math.min(100, (requestsUsed.value / 500) * 100));
});

const hasChanges = computed(() => {
  return localUserId.value !== config.value.taddy_user_id ||
         localApiKey.value !== config.value.taddy_api_key;
});

// Check if credentials are saved (from store, not local inputs)
const hasCredentials = computed(() => {
  return config.value.taddy_user_id && config.value.taddy_api_key;
});

onMounted(() => {
  localUserId.value = config.value.taddy_user_id;
  localApiKey.value = config.value.taddy_api_key;
});

// Sync local fields when store credentials change (e.g. from WS event handled in App.vue)
watch(config, (newConfig) => {
  localUserId.value = newConfig.taddy_user_id;
  localApiKey.value = newConfig.taddy_api_key;
});

async function handleTestConnection() {
  if (!localUserId.value || !localApiKey.value) {
    return;
  }

  isValidating.value = true;
  errorMessage.value = '';

  const result = await apiCall.post('/api/settings/podcast-credentials/validate', {
    taddy_user_id: localUserId.value,
    taddy_api_key: localApiKey.value,
  }, {
    category: 'podcast',
    message: 'Error testing connection'
  });

  if (result.ok && result.data.valid) {
    await updateSetting('podcast-credentials', {
      taddy_user_id: localUserId.value,
      taddy_api_key: localApiKey.value
    });
    await settingsStore.refreshPodcastCredentialsStatus();
  } else {
    errorMessage.value = t('podcastSettings.invalidCredentials');
  }
  isValidating.value = false;
}

</script>

<style scoped>
.credentials-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.form-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-label {
  color: var(--color-brand);
}

.usage-display {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  border-radius: var(--radius-03);
  margin-top: var(--space-04);
  margin-bottom: var(--space-03);
}

.usage-header {
  display: flex;
  justify-content: space-between;
}

.usage-value {
  color: var(--color-text);
}

.progress-bar {
  width: 100%;
  height: 6px;
  background: var(--color-background);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--color-brand);
  border-radius: var(--radius-full);
  transition: width var(--transition-normal);
}

.usage-description {
  color: var(--color-text-secondary);
}

.action-buttons-sticky {
  display: flex;
  gap: var(--space-02);
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

.action-buttons-sticky > * {
  flex: 1;
}

.status-message {
  padding: var(--space-02) var(--space-03);
  border-radius: var(--radius-03);
  font-size: var(--text-size-small);
}

.status-message.error {
  background: var(--color-error-subtle);
  color: var(--color-error);
  border: 1px solid rgba(231, 76, 60, 0.3);
}

@media (max-aspect-ratio: 4/3) {
  .usage-header {
    flex-direction: column;
  }
}
</style>
