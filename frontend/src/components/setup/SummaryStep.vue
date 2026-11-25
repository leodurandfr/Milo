<!-- frontend/src/components/setup/SummaryStep.vue -->
<template>
  <div class="summary-step">
    <!-- milo.local access hint -->
    <div v-if="wifiSsid && wifiSsid !== 'ethernet'" class="summary-hint text-mono-small">
      {{ t('setup.summary.accessHint', { ssid: wifiSsid }) }}
    </div>

    <div class="summary-item">
      <span class="text-mono-medium summary-item__label">{{ t('setup.summary.network') }}</span>
      <div class="summary-item__card">
        <span class="heading-3">{{ wifiSsid === 'ethernet' ? t('network.ethernet') : wifiSsid }}</span>
      </div>
    </div>

    <div class="summary-item">
      <span class="text-mono-medium summary-item__label">{{ t('setup.summary.language') }}</span>
      <div class="summary-item__card">
        <img v-if="flagIcon" :src="flagIcon" :alt="languageLabel" class="summary-item__flag" />
        <span class="heading-3">{{ languageLabel }}</span>
      </div>
    </div>

    <div class="summary-item">
      <span class="text-mono-medium summary-item__label">{{ t('setup.summary.audioCard') }}</span>
      <div class="summary-item__card">
        <span class="heading-3">{{ audioLabel }}</span>
      </div>
      <div v-if="isDac && !volumeControl" class="summary-item__card summary-item__card--secondary">
        <span class="text-mono-medium">{{ t('setup.summary.volumeNotManaged') }}</span>
      </div>
    </div>

    <div class="summary-item">
      <span class="text-mono-medium summary-item__label">{{ t('setup.summary.screen') }}</span>
      <div class="summary-item__card">
        <span class="heading-3">{{ screenLabel }}</span>
      </div>
    </div>

    <!-- A refusal says what failed in the reader's language, the server's
         words folded under it, as a settings screen does. -->
    <NoticeBox v-if="applyRefusal !== null" :detail="applyRefusal">{{ t('setup.summary.applyFailed') }}</NoticeBox>
    <NoticeBox v-if="rebootTimedOut">{{ t('setup.summary.rebootTimeout') }}</NoticeBox>

    <p v-if="isRebooting" class="text-mono-medium text-secondary">
      {{ t('setup.summary.rebootingDescription') }}
    </p>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import NoticeBox from '@/components/ui/NoticeBox.vue';

import { languageFlag } from '@/constants/languageFlags';

const { t } = useI18n();

const props = defineProps({
  wifiSsid: {
    type: String,
    default: null,
  },
  languageCode: {
    type: String,
    default: 'english',
  },
  languageLabel: {
    type: String,
    default: '',
  },
  audioLabel: {
    type: String,
    default: '',
  },
  volumeControl: {
    type: Boolean,
    default: true,
  },
  isDac: {
    type: Boolean,
    default: false,
  },
  screenLabel: {
    type: String,
    default: '',
  },
  isRebooting: {
    type: Boolean,
    default: false,
  },
  // The server's detail for an apply it refused; null while none was.
  applyRefusal: {
    type: String,
    default: null,
  },
  rebootTimedOut: {
    type: Boolean,
    default: false,
  },
});

const flagIcon = computed(() => languageFlag(props.languageCode) || null);
</script>

<style scoped>
.summary-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.summary-item__label {
  color: var(--color-text-secondary);
}

.summary-item__card {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  background: var(--color-inset);
  border-radius: var(--radius-04);
}

.summary-item__card--secondary {
  justify-content: space-between;
}
.summary-item__card--secondary .text-mono-medium {
  color: var(--color-text-secondary);
}

.summary-item__flag {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
}

.summary-hint {
  color: var(--color-text-secondary);
  padding: var(--space-03) var(--space-04);
  background: var(--color-inset);
  border-radius: var(--radius-04);
}

.text-secondary {
  color: var(--color-text-secondary);
}
</style>
