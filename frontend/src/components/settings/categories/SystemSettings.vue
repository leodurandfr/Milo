<!-- frontend/src/components/settings/categories/SystemSettings.vue -->
<!-- Remote access + account password. The two are one decision: the SSH switch
     stays inert until the factory password has been replaced, because that
     password is identical on every Milō and SSH is the only remote path that
     would accept it. The backend refuses the same call with a 409 — the
     disabled switch is the courtesy, not the rule. -->
<template>
  <SettingsContainer>
    <SettingsSection>
      <template #header>
        <div class="system-header">
          <h2 class="heading-2">{{ t('system.ssh.title') }}</h2>
          <Toggle :model-value="ssh.enabled" :disabled="sshToggleDisabled" @change="handleSshToggle" />
        </div>
      </template>

      <span class="text-mono-medium system-description">{{ t('system.ssh.description') }}</span>

      <div v-if="ssh.passwordIsDefault" class="system-notice text-mono-medium">
        {{ t('system.ssh.passwordRequired') }}
      </div>
      <div v-else-if="ssh.enabled" class="system-command text-mono-medium">
        ssh milo@milo.local
      </div>

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

    <SettingsSection :title="t('system.reset.title')">
      <span class="text-mono-medium system-description">{{ t('system.reset.description') }}</span>

      <Button :variant="confirmReset ? 'important' : 'background-strong'" :loading="resetting"
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

const newPassword = ref('');
const confirmPassword = ref('');
const savingPassword = ref(false);
const passwordSaved = ref(false);
const passwordError = ref(null);

// Turning it OFF must stay reachable even while the password is the factory
// one — only opening the door is gated.
const sshToggleDisabled = computed(() =>
  sshBusy.value || (!ssh.enabled && ssh.passwordIsDefault)
);

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
