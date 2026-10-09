<!-- frontend/src/components/settings/categories/SystemPasswordSettings.vue -->
<!-- The account password, one level under System › SSH. The save goes through
     the system store, which outlives this view: System re-reads the SSH state
     when it mounts again, and that read waits for a save still in flight. -->
<template>
  <SectionStack>
    <SectionCard :description="t('system.password.description')">
      <InputText v-model="newPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.newPlaceholder')" />
      <InputText v-model="confirmPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.confirmPlaceholder')" @submit="savePassword" />

      <span v-if="passwordError" class="password-error text-mono-small">{{ passwordError }}</span>

      <Button variant="brand" :loading="savingPassword" :disabled="!canSavePassword || savingPassword"
        @click="savePassword">
        {{ passwordSaved ? t('system.password.saved') : t('system.password.save') }}
      </Button>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useSystemStore } from '@/stores/systemStore';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const timer = useTimer();
const systemStore = useSystemStore();

const PASSWORD_MIN_LENGTH = 8;

const newPassword = ref('');
const confirmPassword = ref('');
const savingPassword = ref(false);
const passwordSaved = ref(false);
const passwordError = ref(null);

const canSavePassword = computed(() =>
  newPassword.value.length >= PASSWORD_MIN_LENGTH && confirmPassword.value.length > 0
);

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
  const saved = await systemStore.setPassword(newPassword.value, { errorRef: passwordError });
  savingPassword.value = false;

  if (!saved) return;

  newPassword.value = '';
  confirmPassword.value = '';
  passwordSaved.value = true;
  timer.setTimeout(() => { passwordSaved.value = false; }, 3000);
}
</script>

<style scoped>
.password-error {
  color: var(--color-error);
}
</style>
