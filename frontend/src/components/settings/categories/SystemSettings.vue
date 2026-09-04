<!-- frontend/src/components/settings/categories/SystemSettings.vue -->
<!-- SSH + account password. The factory password is identical on every Milō and
     published with the source, so an open SSH door is worth a word — but as a
     fact, and only while the door is open. Not as a precondition: nothing on the
     API authenticates, so refusing to open it would have stopped the owner and
     nobody else. -->
<template>
  <SettingsContainer>
    <SettingsSection>
      <template #header>
        <div class="system-header">
          <h2 class="heading-2">{{ t('system.ssh.title') }}</h2>
          <Toggle :model-value="ssh.enabled" :disabled="sshBusy" @change="handleSshToggle" />
        </div>
      </template>

      <span class="text-mono-medium system-description">{{ t('system.ssh.description') }}</span>

      <template v-if="ssh.enabled">
        <div class="system-command text-mono-medium">
          ssh milo@milo.local
        </div>
        <div v-if="ssh.passwordIsDefault" class="system-notice text-mono-medium">
          {{ t('system.ssh.factoryPassword') }}
        </div>
      </template>

      <span v-if="sshError" class="system-error text-mono-small">{{ sshError }}</span>
    </SettingsSection>

    <SettingsSection :title="t('system.password.title')">
      <span class="text-mono-medium system-description">{{ t('system.password.description') }}</span>

      <InputText v-model="newPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.newPlaceholder')" />
      <InputText v-model="confirmPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.confirmPlaceholder')" @submit="savePassword" />

      <span v-if="passwordError" class="system-error text-mono-small">{{ passwordError }}</span>

      <Button variant="brand" :loading="savingPassword" :disabled="!canSavePassword || savingPassword"
        @click="savePassword">
        {{ passwordSaved ? t('system.password.saved') : t('system.password.save') }}
      </Button>
    </SettingsSection>

    <SettingsSection :title="t('system.diagnostic.title')">
      <span class="text-mono-medium system-description">{{ t('system.diagnostic.description') }}</span>

      <Button variant="outline" :loading="generating" :disabled="generating"
        @click="generateReport">
        {{ generating ? t('system.diagnostic.generating') : t('system.diagnostic.generate') }}
      </Button>

      <span v-if="diagnosticError" class="system-error text-mono-small">{{ diagnosticError }}</span>

      <template v-if="report">
        <!-- Download and Copy are both here because neither one reaches
             everybody: on the Pi's own screen a download lands in a filesystem
             the user cannot open, and from a phone the page is served over
             plain http, where the clipboard API does not exist. -->
        <div class="diagnostic-actions">
          <Button variant="background-neutral" @click="downloadReport">
            {{ t('system.diagnostic.download') }}
          </Button>
          <Button variant="background-neutral" @click="copyReport">
            {{ copied ? t('system.diagnostic.copied') : t('system.diagnostic.copy') }}
          </Button>
        </div>

        <span v-if="copyError" class="system-error text-mono-small">{{ copyError }}</span>

        <ul v-if="unavailable.length" class="diagnostic-missing text-mono-small">
          <li class="diagnostic-missing__title">{{ t('system.diagnostic.notCollected') }}</li>
          <li v-for="item in unavailable" :key="item.section">{{ item.section }} — {{ item.reason }}</li>
        </ul>

        <button v-press type="button" class="diagnostic-disclosure text-mono-medium"
          @click="showPreview = !showPreview">
          {{ showPreview ? t('system.diagnostic.hidePreview') : t('system.diagnostic.showPreview') }}
        </button>
        <pre v-if="showPreview" class="diagnostic-preview text-mono-small">{{ report }}</pre>
      </template>
    </SettingsSection>

    <SettingsSection :title="t('system.reset.title')">
      <span class="text-mono-medium system-description">{{ t('system.reset.description') }}</span>

      <!-- Red from the first press, not only once armed: the action is
           destructive whether or not it is confirmed yet, and the two-step is
           carried by the label. -->
      <Button variant="important" :loading="resetting"
        :disabled="resetting" @click="handleReset">
        {{ resetLabel }}
      </Button>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import { useTimer } from '@/composables/useTimer';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import Toggle from '@/components/ui/Toggle.vue';

const { t } = useI18n();
const timer = useTimer();

const PASSWORD_MIN_LENGTH = 8;

const ssh = reactive({ enabled: false, active: false, passwordIsDefault: true });
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

const newPassword = ref('');
const confirmPassword = ref('');
const savingPassword = ref(false);
const passwordSaved = ref(false);
const passwordError = ref(null);

const canSavePassword = computed(() =>
  newPassword.value.length >= PASSWORD_MIN_LENGTH && confirmPassword.value.length > 0
);

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

function applySshState(data) {
  ssh.enabled = !!data.enabled;
  ssh.active = !!data.active;
  ssh.passwordIsDefault = !!data.password_is_default;
}

async function loadSshState() {
  const result = await apiCall.get('/api/system/ssh', {
    category: 'system',
    message: 'Failed to load SSH state'
  });
  if (result.ok) applySshState(result.data.data);
}

async function handleSshToggle(enabled) {
  sshBusy.value = true;
  sshError.value = null;
  const result = await apiCall.put('/api/system/ssh', { enabled }, {
    category: 'system',
    message: 'Failed to change SSH state',
    errorRef: sshError
  });
  if (result.ok) {
    applySshState(result.data.data);
  } else {
    // The switch never moved server-side; re-read rather than trust the click.
    await loadSshState();
  }
  sshBusy.value = false;
}

async function savePassword() {
  passwordError.value = null;

  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = t('system.password.mismatch');
    return;
  }
  if (newPassword.value.length < PASSWORD_MIN_LENGTH) {
    passwordError.value = t('system.password.tooShort', { n: PASSWORD_MIN_LENGTH });
    return;
  }

  savingPassword.value = true;
  const result = await apiCall.post('/api/system/password', { password: newPassword.value }, {
    category: 'system',
    message: 'Failed to set device password',
    errorRef: passwordError
  });
  savingPassword.value = false;

  if (!result.ok) return;

  newPassword.value = '';
  confirmPassword.value = '';
  ssh.passwordIsDefault = false;
  passwordSaved.value = true;
  timer.setTimeout(() => { passwordSaved.value = false; }, 3000);
}

onMounted(loadSshState);
</script>

<style scoped>
.system-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

.system-description {
  color: var(--color-text-secondary);
}

.system-notice {
  padding: var(--space-03);
  border-radius: var(--radius-04);
  background: var(--color-warning-subtle);
  color: var(--color-warning);
}

.diagnostic-actions {
  display: flex;
  gap: var(--space-03);
}

.diagnostic-missing {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  margin: 0;
  padding: var(--space-03);
  border-radius: var(--radius-04);
  background: var(--color-warning-subtle);
  color: var(--color-warning);
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
  background: var(--color-background-strong);
  color: var(--color-text-secondary);
  white-space: pre;
}

.system-command {
  padding: var(--space-03);
  border-radius: var(--radius-04);
  background: var(--color-background);
  color: var(--color-text-secondary);
  overflow-x: auto;
  white-space: nowrap;
}

.system-error {
  color: var(--color-error);
}
</style>
