<!-- frontend/src/components/settings/categories/SystemSettings.vue -->
<!-- SSH, with the account password one level down (SshSection says why the
     factory password is a warning and not a precondition). -->
<template>
  <SectionStack>
    <SystemInfoSection />

    <SshSection :state="ssh" host="milo.local" user="milo" :busy="sshBusy" :error="sshError"
      :description="t('system.ssh.description')"
      :factory-password-notice="t('system.ssh.factoryPassword')"
      @toggle="handleSshToggle" @open-password="$emit('open-password')" />

    <SectionCard :title="t('system.diagnostic.title')" :description="t('system.diagnostic.description')">
      <Button variant="brand" :loading="generating" :disabled="generating"
        @click="generateReport">
        {{ generating ? t('system.diagnostic.generating') : t('system.diagnostic.generate') }}
      </Button>

      <NoticeBox v-if="diagnosticError">{{ diagnosticError }}</NoticeBox>

      <template v-if="report">
        <!-- Download and Copy are both here because neither one reaches
             everybody: on the Pi's own screen a download lands in a filesystem
             the user cannot open, and from a phone the page is served over
             plain http, where the clipboard API does not exist. -->
        <div class="diagnostic-actions">
          <Button variant="control" @click="downloadReport">
            {{ t('system.diagnostic.download') }}
          </Button>
          <Button variant="control" @click="copyReport">
            {{ copied ? t('system.diagnostic.copied') : t('system.diagnostic.copy') }}
          </Button>
        </div>

        <NoticeBox v-if="copyError">{{ copyError }}</NoticeBox>

        <NoticeBox v-if="unavailable.length" kind="warning">
          <ul class="diagnostic-missing text-mono-small">
            <li class="diagnostic-missing__title">{{ t('system.diagnostic.notCollected') }}</li>
            <li v-for="item in unavailable" :key="item.section">{{ item.section }} — {{ item.reason }}</li>
          </ul>
        </NoticeBox>

        <button v-press type="button" class="diagnostic-disclosure text-mono-medium"
          @click="showPreview = !showPreview">
          {{ showPreview ? t('system.diagnostic.hidePreview') : t('system.diagnostic.showPreview') }}
        </button>
        <pre v-if="showPreview" class="diagnostic-preview text-mono-small">{{ report }}</pre>
      </template>
    </SectionCard>

    <SectionCard :title="t('system.reset.title')" :description="t('system.reset.description')">
      <!-- Red from the first press, not only once armed: the action is
           destructive whether or not it is confirmed yet, and the two-step is
           carried by the label. -->
      <Button variant="important" :loading="resetting"
        :disabled="resetting" @click="handleReset">
        {{ resetLabel }}
      </Button>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import { useTimer } from '@/composables/useTimer';
import { useSystemStore } from '@/stores/systemStore';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import Button from '@/components/ui/Button.vue';
import SystemInfoSection from '@/components/settings/categories/SystemInfoSection.vue';
import SshSection from '@/components/settings/categories/SshSection.vue';

defineEmits(['open-password']);

const { t } = useI18n();
const timer = useTimer();
const systemStore = useSystemStore();
const { ssh } = storeToRefs(systemStore);

const sshBusy = ref(false);
const sshError = ref(null);

const confirmReset = ref(false);
const resetting = ref(false);

const report = ref(null);
const unavailable = ref([]);
const generating = ref(false);
const showPreview = ref(false);
const copied = ref(false);
const copyError = ref(null);
const diagnosticError = ref(null);

const resetLabel = computed(() => {
  if (resetting.value) return t('system.reset.running');
  if (confirmReset.value) return t('system.reset.confirm');
  return t('system.reset.action');
});

/** Two-step, like every other irreversible action here: the first press only
 *  changes the label. */
async function handleReset() {
  if (!confirmReset.value) {
    confirmReset.value = true;
    return;
  }
  confirmReset.value = false;
  resetting.value = true;

  const result = await apiCall.post('/api/system/reset-setup', null, {
    category: 'system',
    message: 'Failed to reset the setup'
  });
  // The unit reboots into the wizard; leave the button in its running state
  // until the page goes away with it.
  if (!result.ok) resetting.value = false;
}

/** The report is built server-side and handed back whole; nothing about it is
 *  stored or broadcast, so there is no store and no WS event to keep in sync. */
async function generateReport() {
  generating.value = true;
  diagnosticError.value = null;
  copyError.value = null;

  const result = await apiCall.post('/api/system/diagnostic', null, {
    category: 'system',
    message: 'Failed to generate the diagnostic report',
    errorRef: diagnosticError,
    // The server caps itself at 20 s; this only has to outlast that.
    timeout: 30000
  });
  generating.value = false;

  if (!result.ok) return;
  report.value = result.data.data.report;
  unavailable.value = result.data.data.unavailable;
  showPreview.value = false;
}

function downloadReport() {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  const url = URL.createObjectURL(new Blob([report.value], { type: 'text/plain' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `milo-diagnostic-${stamp}.txt`;
  link.click();
  URL.revokeObjectURL(url);
}

async function copyReport() {
  copyError.value = null;
  if (await writeToClipboard(report.value)) {
    copied.value = true;
    timer.setTimeout(() => { copied.value = false; }, 3000);
  } else {
    copyError.value = t('system.diagnostic.copyFailed');
  }
}

/** navigator.clipboard exists only in a secure context. The kiosk browses
 *  http://localhost, which is one; a phone browsing http://milo.local is not,
 *  and there the selection-based path is the only one that works. */
async function writeToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through: a permission refusal still leaves the path below.
    }
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}

async function handleSshToggle(enabled) {
  sshBusy.value = true;
  sshError.value = null;
  await systemStore.setSshEnabled(enabled, { errorRef: sshError });
  sshBusy.value = false;
}

onMounted(systemStore.loadSsh);
</script>

<style scoped>
.diagnostic-actions {
  display: flex;
  gap: var(--space-03);
}

.diagnostic-missing {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  margin: 0;
  padding: 0;
  list-style: none;
}

.diagnostic-missing__title {
  color: var(--color-text-secondary);
}

.diagnostic-disclosure {
  align-self: flex-start;
  padding: 0;
  border: none;
  background: none;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.diagnostic-preview {
  max-height: 40vh;
  margin: 0;
  padding: var(--space-03);
  overflow: auto;
  border-radius: var(--radius-04);
  background: var(--color-inset);
  color: var(--color-text-secondary);
  white-space: pre;
}
</style>
