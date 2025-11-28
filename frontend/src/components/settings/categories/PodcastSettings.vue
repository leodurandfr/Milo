<!-- frontend/src/components/settings/categories/PodcastSettings.vue -->
<template>
  <section class="settings-section">
    <div class="podcast-group">
      <h2 class="heading-2 heading-3">{{ t('podcastSettings.taddyCredentials') }}</h2>
      <div class="setting-item-container">
        <div class="podcast-description text-mono">
          {{ t('podcastSettings.credentialsDescription') }}
        </div>

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

          <!-- API Usage Display -->
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

          <!-- Error message -->
          <div v-if="errorMessage" class="status-message error text-mono">
            {{ errorMessage }}
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting } = useSettingsAPI();
const settingsStore = useSettingsStore();

const localUserId = ref('');
const localApiKey = ref('');
const isValidating = ref(false);
const errorMessage = ref('');

// Read usage from settingsStore (loaded at app startup)
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

// Calculate API reset date (1 month after validation)
const resetDateText = computed(() => {
  if (!credentialsValidatedAt.value) return null;

  const validatedDate = new Date(credentialsValidatedAt.value * 1000);
  const resetDate = new Date(validatedDate);
  resetDate.setMonth(resetDate.getMonth() + 1);

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

// Check if there are changes
const hasChanges = computed(() => {
  return localUserId.value !== config.value.taddy_user_id ||
         localApiKey.value !== config.value.taddy_api_key;
});

// Check if credentials are saved (from store, not local inputs)
const hasCredentials = computed(() => {
  return config.value.taddy_user_id && config.value.taddy_api_key;
});

// Initialize local values
onMounted(() => {
  localUserId.value = config.value.taddy_user_id;
  localApiKey.value = config.value.taddy_api_key;
});

// WebSocket listener
const handleCredentialsChanged = (msg) => {
  if (msg.data?.config) {
    settingsStore.updatePodcastCredentials({
      taddy_user_id: msg.data.config.taddy_user_id || '',
      taddy_api_key: msg.data.config.taddy_api_key || ''
    });
    // Update local values
    localUserId.value = msg.data.config.taddy_user_id || '';
    localApiKey.value = msg.data.config.taddy_api_key || '';
  }
};

async function handleTestConnection() {
  if (!localUserId.value || !localApiKey.value) {
    return;
  }

  try {
    isValidating.value = true;
    errorMessage.value = '';

    const response = await fetch('/api/settings/podcast-credentials/validate', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        taddy_user_id: localUserId.value,
        taddy_api_key: localApiKey.value,
      }),
    });

    const data = await response.json();

    if (data.valid) {
      // Valid credentials - save them automatically
      await updateSetting('podcast-credentials', {
        taddy_user_id: localUserId.value,
        taddy_api_key: localApiKey.value
      });
      // Refresh credentials status (updates podcastApiUsage and podcastCredentialsStatus)
      await settingsStore.refreshPodcastCredentialsStatus();
    } else {
      // Invalid credentials
      errorMessage.value = t('podcastSettings.invalidCredentials');
    }
  } catch (error) {
    console.error('Error testing connection:', error);
    errorMessage.value = t('podcastSettings.invalidCredentials');
  } finally {
    isValidating.value = false;
  }
}

onMounted(() => {
  on('settings', 'podcast_credentials_changed', handleCredentialsChanged);
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.podcast-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.podcast-description {
  color: var(--color-text-secondary);
}

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

.status-message.success {
  background: rgba(46, 204, 113, 0.1);
  color: #2ecc71;
  border: 1px solid rgba(46, 204, 113, 0.3);
}

.status-message.error {
  background: rgba(231, 76, 60, 0.1);
  color: #e74c3c;
  border: 1px solid rgba(231, 76, 60, 0.3);
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .usage-header {
    flex-direction: column;
  }
}
</style>
