<!-- frontend/src/components/settings/categories/PodcastSettings.vue -->
<template>
  <section class="settings-section">
    <div class="podcast-group">
      <h2 class="heading-2 text-body">{{ t('podcastSettings.taddyCredentials') }}</h2>
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

          <!-- API Usage Display - Visible only after successful validation -->
          <div v-if="requestsRemaining !== null" class="usage-display">
            <div class="usage-header">
              <label class="form-label text-mono">{{ t('podcastSettings.apiUsage') }}</label>
              <span class="usage-value text-mono">{{ requestsRemaining }} / 500</span>
            </div>
            <div class="progress-bar">
              <div class="progress-fill" :style="{ width: usagePercentage + '%' }"></div>
            </div>
            <span class="usage-description text-mono">{{ t('podcastSettings.requestsThisMonth') }}</span>
          </div>

          <div class="action-buttons">
            <Button
              variant="primary"
              :disabled="!hasChanges"
              @click="handleSave"
            >
              {{ t('podcastSettings.saveButton') }}
            </Button>
            <Button
              v-if="hasChanges || requestsRemaining === null"
              variant="outline"
              :disabled="isValidating || !localUserId || !localApiKey"
              :loading="isValidating"
              @click="handleValidate"
            >
              {{ t('podcastSettings.validateButton') }}
            </Button>
          </div>

          <div v-if="saveStatus === 'success'" class="status-message success text-mono">
            {{ t('podcastSettings.saveSuccess') }}
          </div>
          <div v-if="saveStatus === 'error'" class="status-message error text-mono">
            {{ t('podcastSettings.saveError') }}
          </div>
          <div v-if="validationStatus === 'success'" class="status-message success text-mono">
            {{ t('podcastSettings.validationSuccess') }}
            <span v-if="requestsRemaining !== null" class="requests-info">
              ({{ requestsRemaining }}/500 {{ t('podcastSettings.requestsThisMonth') }})
            </span>
          </div>
          <div v-if="validationStatus === 'error'" class="status-message error text-mono">
            {{ validationMessage || t('podcastSettings.validationError') }}
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
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
const saveStatus = ref('');
const isValidating = ref(false);
const validationStatus = ref('');
const validationMessage = ref('');
const requestsRemaining = ref(null);

// Load current credentials
const config = computed(() => ({
  taddy_user_id: settingsStore.podcastCredentials?.taddy_user_id || '',
  taddy_api_key: settingsStore.podcastCredentials?.taddy_api_key || ''
}));

// Calculate usage percentage (remaining requests out of 500)
const usagePercentage = computed(() => {
  if (requestsRemaining.value === null) return 0;
  return Math.max(0, Math.min(100, (requestsRemaining.value / 500) * 100));
});

// Check if there are changes
const hasChanges = computed(() => {
  return localUserId.value !== config.value.taddy_user_id ||
         localApiKey.value !== config.value.taddy_api_key;
});

// Load API usage from backend
async function loadUsage() {
  try {
    const response = await fetch('/api/settings/podcast-credentials/usage');
    const data = await response.json();

    if (data.status === 'success' && data.requests_remaining !== null) {
      requestsRemaining.value = data.requests_remaining;
    }
  } catch (error) {
    console.error('Error loading API usage:', error);
  }
}

// Initialize local values
onMounted(() => {
  localUserId.value = config.value.taddy_user_id;
  localApiKey.value = config.value.taddy_api_key;

  // Load usage if credentials exist
  if (localUserId.value && localApiKey.value) {
    loadUsage();
  }
});

async function handleSave() {
  try {
    saveStatus.value = '';
    validationStatus.value = '';

    await updateSetting('podcast-credentials', {
      taddy_user_id: localUserId.value,
      taddy_api_key: localApiKey.value
    });

    saveStatus.value = 'success';
    setTimeout(() => {
      saveStatus.value = '';
    }, 3000);
  } catch (error) {
    console.error('Error saving podcast credentials:', error);
    saveStatus.value = 'error';
    setTimeout(() => {
      saveStatus.value = '';
    }, 3000);
  }
}

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

async function handleValidate() {
  if (!localUserId.value || !localApiKey.value) {
    validationStatus.value = 'error';
    validationMessage.value = t('podcastSettings.validationEmpty');
    setTimeout(() => {
      validationStatus.value = '';
      validationMessage.value = '';
    }, 3000);
    return;
  }

  try {
    isValidating.value = true;
    saveStatus.value = '';
    validationStatus.value = '';
    validationMessage.value = '';

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
      validationStatus.value = 'success';
      requestsRemaining.value = data.requests_remaining;
      validationMessage.value = data.message || t('podcastSettings.validationSuccess');
    } else {
      validationStatus.value = 'error';
      requestsRemaining.value = null;
      validationMessage.value = data.message || t('podcastSettings.validationError');
    }

    setTimeout(() => {
      validationStatus.value = '';
      validationMessage.value = '';
    }, 5000);
  } catch (error) {
    console.error('Error validating credentials:', error);
    validationStatus.value = 'error';
    validationMessage.value = t('podcastSettings.validationNetworkError');
    setTimeout(() => {
      validationStatus.value = '';
      validationMessage.value = '';
    }, 5000);
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
  color: var(--color-text-primary);
  font-weight: 500;
}

.usage-display {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  padding: var(--space-03);
  background: var(--color-background-strong);
  border-radius: var(--radius-03);
  margin-top: var(--space-02);
}

.usage-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.usage-value {
  color: var(--color-text);
  font-weight: 600;
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
  font-size: var(--text-size-small);
}

.action-buttons {
  display: flex;
  gap: var(--space-02);
  margin-top: var(--space-02);
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

.requests-info {
  display: block;
  margin-top: var(--space-01);
  font-size: var(--text-size-small);
  opacity: 0.8;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .action-buttons {
    flex-direction: column;
  }
}
</style>
