<!-- frontend/src/components/setup/SummaryStep.vue -->
<template>
  <div class="summary-step">
    <!-- milo.local access hint -->
    <div v-if="wifiSsid && wifiSsid !== 'ethernet'" class="summary-hint text-mono-small">
      {{ t('setup.summary.accessHint', { ssid: wifiSsid }) }}
    </div>

    <!-- Network -->
    <div class="summary-item">
      <span class="text-mono summary-item__label">{{ t('setup.summary.network') }}</span>
      <div class="summary-item__card">
        <span class="heading-3">{{ wifiSsid === 'ethernet' ? t('network.ethernet') : wifiSsid }}</span>
      </div>
    </div>

    <!-- Language -->
    <div class="summary-item">
      <span class="text-mono summary-item__label">{{ t('setup.summary.language') }}</span>
      <div class="summary-item__card">
        <img v-if="flagIcon" :src="flagIcon" :alt="languageLabel" class="summary-item__flag" />
        <span class="heading-3">{{ languageLabel }}</span>
      </div>
    </div>

    <!-- Audio card -->
    <div class="summary-item">
      <span class="text-mono summary-item__label">{{ t('setup.summary.audioCard') }}</span>
      <div class="summary-item__card">
        <span class="heading-3">{{ audioLabel }}</span>
      </div>
      <div v-if="isDac && !volumeControl" class="summary-item__card summary-item__card--secondary">
        <span class="text-mono">{{ t('setup.summary.volumeNotManaged') }}</span>
      </div>
    </div>

    <!-- Screen -->
    <div class="summary-item">
      <span class="text-mono summary-item__label">{{ t('setup.summary.screen') }}</span>
      <div class="summary-item__card">
        <span class="heading-3">{{ screenLabel }}</span>
      </div>
    </div>

    <p v-if="error" class="text-mono error-message">{{ error }}</p>

    <p v-if="isRebooting" class="text-mono text-secondary">
      {{ t('setup.summary.rebootingDescription') }}
    </p>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';

import franceIcon from '@/assets/flags-icons/france.svg';
import unitedKingdomIcon from '@/assets/flags-icons/united-kingdom.svg';
import spainIcon from '@/assets/flags-icons/spain.svg';
import indiaIcon from '@/assets/flags-icons/india.svg';
import chinaIcon from '@/assets/flags-icons/china.svg';
import portugalIcon from '@/assets/flags-icons/portugal.svg';
import italyIcon from '@/assets/flags-icons/italy.svg';
import germanyIcon from '@/assets/flags-icons/germany.svg';

const flagIcons = {
  french: franceIcon,
  english: unitedKingdomIcon,
  spanish: spainIcon,
  hindi: indiaIcon,
  chinese: chinaIcon,
  portuguese: portugalIcon,
  italian: italyIcon,
  german: germanyIcon,
};

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
  error: {
    type: String,
    default: null,
  },
});

const flagIcon = computed(() => flagIcons[props.languageCode] || null);
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
  background: var(--color-background);
  border-radius: var(--radius-04);
}

.summary-item__card--secondary {
  justify-content: space-between;
}
.summary-item__card--secondary .text-mono {
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
  background: var(--color-background);
  border-radius: var(--radius-04);
}

.error-message {
  color: var(--color-error);
}

.text-secondary {
  color: var(--color-text-secondary);
}
</style>
