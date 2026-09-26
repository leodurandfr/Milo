<!-- frontend/src/components/settings/categories/MacSettings.vue -->
<template>
  <SettingsContainer>
    <template v-if="caps">
      <!-- The half this unit runs: roc-recv. -->
      <SettingsSection :title="t('macSettings.receiver')">
        <SettingItem :label="t('macSettings.targetLatency')">
          <RangeSlider :model-value="draft.target_latency_ms" :min="caps.target_latency_ms.min"
            :max="caps.target_latency_ms.max" :step="5" value-unit="ms" :disabled="busy"
            @update:model-value="set('target_latency_ms', $event)" />
        </SettingItem>

        <SettingItem :label="t('macSettings.latencyProfile')">
          <ButtonGroup :model-value="draft.latency_profile" :options="profileOptions" :disabled="busy"
            mobile-layout="column" @change="set('latency_profile', $event)" />
        </SettingItem>

        <SettingItem :label="t('macSettings.frameLength')">
          <ButtonGroup :model-value="draft.frame_length_ms" :options="msOptions(caps.frame_lengths)"
            :disabled="busy" mobile-layout="grid-3" @change="set('frame_length_ms', $event)" />
        </SettingItem>
      </SettingsSection>

      <!-- The half each Mac runs: its roc-vad device, rebuilt by the Milō app
           for Mac when this changes. -->
      <SettingsSection :title="t('macSettings.sender')">
        <p class="text-mono-medium section-note">{{ t('macSettings.senderNote') }}</p>

        <SettingItem :label="t('macSettings.packetLength')">
          <ButtonGroup :model-value="draft.packet_length_ms" :options="msOptions(caps.packet_lengths)"
            :disabled="busy" mobile-layout="grid-3" @change="set('packet_length_ms', $event)" />
        </SettingItem>

        <SettingItem :label="t('macSettings.fecSource')">
          <RangeSlider :model-value="draft.fec_block_source" :min="caps.fec_block_source.min"
            :max="caps.fec_block_source.max" :disabled="busy"
            @update:model-value="set('fec_block_source', $event)" />
        </SettingItem>

        <SettingItem :label="t('macSettings.fecRepair')">
          <RangeSlider :model-value="draft.fec_block_repair" :min="caps.fec_block_repair.min"
            :max="caps.fec_block_repair.max" :disabled="busy"
            @update:model-value="set('fec_block_repair', $event)" />
        </SettingItem>

        <div class="toggle-row">
          <span class="text-mono-medium toggle-row__label">{{ t('macSettings.interleaving') }}</span>
          <Toggle :model-value="draft.packet_interleaving" :disabled="busy"
            @change="set('packet_interleaving', $event)" />
        </div>
      </SettingsSection>

      <!-- The analysis measures and proposes; the controls above move and
           Apply stays the one write. -->
      <SettingsSection :title="t('macSettings.analysis')">
        <Button variant="outline" size="medium" class="auto-tune" :loading="calibration.running"
          :disabled="busy" @click="startAnalysis">
          {{ t('macSettings.autoTune') }}
        </Button>

        <ProgressStrip :open="calibration.running" :percent="progressPercent" :step-ms="PROGRESS_TICK_MS"
          :label="calibration.running ? stageLabel : ''"
          :hint="calibration.running ? t('macSettings.remaining', { seconds: remainingSeconds }) : ''" />

        <p v-if="!calibration.running && analysisNote" class="text-mono-medium analysis-note">
          {{ analysisNote }}
        </p>

        <div v-if="!calibration.running && measured" class="analysis-grid">
          <div class="analysis-item">
            <span class="heading-4 analysis-item__name">{{ measured.mac_name }}</span>
            <div class="analysis-item__metrics">
              <div class="analysis-item__metric">
                <span class="text-mono-medium analysis-item__label">{{ t('macSettings.roundTrip') }}</span>
                <span class="text-mono-medium analysis-item__value">{{ measured.rtt_max_ms }} ms</span>
              </div>
              <div class="analysis-item__metric">
                <span class="text-mono-medium analysis-item__label">{{ t('macSettings.loss') }}</span>
                <span class="text-mono-medium analysis-item__value"
                  :class="{ 'analysis-item__value--warn': measured.loss_pct > 0 }">{{ measured.loss_pct }} %</span>
              </div>
              <div class="analysis-item__metric">
                <span class="text-mono-medium analysis-item__label">
                  {{ burstAssumed ? t('macSettings.burstAssumed') : t('macSettings.burst') }}
                </span>
                <span class="text-mono-medium analysis-item__value">{{ measured.mac_burst_ms }} ms</span>
              </div>
            </div>
          </div>

          <div class="analysis-item">
            <span class="heading-4 analysis-item__name">{{ t('macSettings.estimate') }}</span>
            <div class="analysis-item__metrics">
              <div class="analysis-item__metric">
                <span class="text-mono-medium analysis-item__label">{{ t('macSettings.current') }}</span>
                <span class="text-mono-medium analysis-item__value">≈ {{ predicted.current }} ms</span>
              </div>
              <div class="analysis-item__metric">
                <span class="text-mono-medium analysis-item__label">{{ t('macSettings.proposed') }}</span>
                <span class="text-mono-medium analysis-item__value analysis-item__value--brand">
                  ≈ {{ predicted.proposed }} ms
                </span>
              </div>
            </div>
          </div>
        </div>
      </SettingsSection>
    </template>

    <p v-else-if="macLinkStore.capabilitiesFailed" class="text-mono-medium section-note">
      {{ t('macSettings.unavailable') }}
    </p>

    <!-- One write for both halves: mac.env and a roc-recv restart here, the
         Mac's device rebuilt by the Milō app for Mac. -->
    <Button v-if="macLinkStore.hasChanges" variant="brand" size="medium" class="apply-button-sticky"
      :loading="macLinkStore.isApplying" :disabled="busy" @click="macLinkStore.apply()">
      {{ macLinkStore.isApplying ? t('macSettings.applying') : t('macSettings.apply') }}
    </Button>
  </SettingsContainer>
</template>

<script setup>
import { computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import { useMacLinkStore } from '@/stores/macLinkStore';
import { useAnalysisRun, PROGRESS_TICK_MS } from '@/composables/useAnalysisRun';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ProgressStrip from '@/components/settings/ProgressStrip.vue';

const { t } = useI18n();
const settingsStore = useSettingsStore();
const macLinkStore = useMacLinkStore();

const caps = computed(() => macLinkStore.capabilities);
const draft = computed(() => macLinkStore.draft);
const calibration = computed(() => macLinkStore.calibration);
const busy = computed(() => macLinkStore.isApplying || calibration.value.running);

function set(key, value) {
  macLinkStore.setDraftValue(key, value);
}

const profileOptions = computed(() => {
  const labels = {
    responsive: t('macSettings.profiles.responsive'),
    gradual: t('macSettings.profiles.gradual'),
    intact: t('macSettings.profiles.intact'),
  };
  return (caps.value?.latency_profiles || []).map((value) => ({ label: labels[value] || value, value }));
});

function msOptions(values) {
  return values.map((value) => ({ label: `${value} ms`, value }));
}

// === ANALYSIS ===

const ANALYSIS_ERROR_KEYS = {
  no_mac: 'failedNoMac',
  several_macs: 'failedSeveralMacs',
  too_few_samples: 'failedTooFewSamples',
  no_ping_reply: 'failedNoPing',
  two_streams: 'failedTwoStreams',
  receiver_not_running: 'failedReceiverStopped',
  receiver_restarted: 'failedReceiverRestarted',
  journal_unreadable: 'failedJournal',
  probe_failed: 'failedProbe',
  start_failed: 'failedStart',
};

const analysisNote = computed(() => {
  if (calibration.value.error) {
    return t(`macSettings.${ANALYSIS_ERROR_KEYS[calibration.value.error] || 'failedProbe'}`,
      { detail: calibration.value.detail || '' });
  }
  if (calibration.value.result?.assumed?.length) return t('macSettings.partlyAssumed');
  return null;
});

const measured = computed(() => calibration.value.result?.measurements || null);
const predicted = computed(() => calibration.value.result?.predicted_latency_ms || {});
const burstAssumed = computed(() => (calibration.value.result?.assumed || []).includes('mac_burst'));

const ANALYSIS_STAGE_KEYS = { measuring: 'stageMeasuring', computing: 'stageComputing' };
const stageLabel = computed(() =>
  t(`macSettings.${ANALYSIS_STAGE_KEYS[calibration.value.stage] || 'stageMeasuring'}`)
);

// A result lands on the controls; Apply stays the one write.
const { progressPercent, remainingSeconds, startAnalysis } = useAnalysisRun(calibration, {
  start: () => macLinkStore.startCalibration(),
  stage: () => macLinkStore.stageCalibrationResult(),
});

// The applied link moves under the panel when another device applies one; the
// controls follow it unless something is being edited here — judged against
// the link they were showing, since against the new one an untouched panel
// reads as edited, and its Apply would have undone the other device's change.
watch(() => settingsStore.macRocSettings, (applied, previous) => {
  macLinkStore.followApplied(previous);
});

onMounted(() => {
  macLinkStore.syncDraft();
  macLinkStore.loadCapabilities();
  macLinkStore.loadCalibration();
});
</script>

<style scoped>
.section-note {
  color: var(--color-text-secondary);
  margin: 0;
}

.toggle-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.toggle-row__label {
  color: var(--color-text-secondary);
}

.auto-tune {
  width: 100%;
}

.analysis-note {
  color: var(--color-text-secondary);
  margin: var(--space-02) 0 0;
}

.analysis-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
  margin-top: var(--space-02);
}

.analysis-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.analysis-item__name {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.analysis-item__metrics {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  padding-top: var(--space-03);
  border-top: 1px solid var(--color-border);
}

.analysis-item__metric {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-02);
}

.analysis-item__label {
  color: var(--color-text-secondary);
}

.analysis-item__value {
  color: var(--color-text);
  white-space: nowrap;
}

.analysis-item__value--warn,
.analysis-item__value--brand {
  color: var(--color-brand);
}

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

@media (max-aspect-ratio: 4/3) {
  .analysis-grid {
    grid-template-columns: 1fr;
  }
}
</style>
