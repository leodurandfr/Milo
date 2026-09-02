<!-- frontend/src/components/settings/categories/music-library/RenameUsb.vue -->
<!--
  Name a USB key — a sub-screen of the Music Library settings, the same depth as
  editing a network server, because it is the same kind of act: naming a music
  origin.

  The name is stored against the key's filesystem UUID, so it comes back when the
  key is plugged in again, and it becomes that key's Navidrome library name — it
  is what the library view's storage filter shows. Clearing the field restores
  the disk label the key came with.

  A key that is unplugged keeps its Navidrome library and its whole index, so a
  replug costs a quick scan rather than re-reading every tag. Forgetting it here
  is what gives that index back — offered only while the key is away, because
  plugging it in would simply index it again.
-->
<template>
  <SettingsContainer>
    <form class="usb-form" @submit.prevent="handleSubmit">
      <SettingsSection>
        <div class="form-group">
          <label class="text-mono-medium">{{ t('musicLibrary.usb.name') }}</label>
          <InputText v-model="name" :placeholder="device?.label || t('musicLibrary.usb.namePlaceholder')"
            :maxlength="128" />
          <span class="text-mono-medium usb-form__hint">
            {{ t('musicLibrary.usb.nameHint', { label: device?.label || '' }) }}
          </span>
        </div>

        <div v-if="errorMessage" class="usb-form__error text-mono-medium">{{ errorMessage }}</div>
      </SettingsSection>

      <SettingsSection v-if="device && !device.mounted">
        <template #header>
          <SectionHeader :title="t('musicLibrary.usb.forgetTitle')" />
        </template>
        <p class="text-mono-medium usb-form__hint">
          {{ t('musicLibrary.usb.forgetDescription', { count: device.track_count || 0 }) }}
        </p>
        <Button variant="background-strong" size="medium" :loading="isForgetting"
          :disabled="isForgetting" @click="handleForget">
          {{ t('musicLibrary.usb.forget') }}
        </Button>
      </SettingsSection>

      <Button v-if="hasChanged" variant="brand" size="medium" type="submit" class="apply-button-sticky"
        :loading="isSubmitting" :disabled="isSubmitting">
        {{ t('musicLibrary.usb.rename') }}
      </Button>
    </form>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';

const props = defineProps({
  device: {
    type: Object,
    default: null,
  },
});

const emit = defineEmits(['success']);

const { t } = useI18n();
const store = useMusicLibraryStore();

// Starts from the current display name, which is the disk label until the key
// has been named once.
const name = ref(props.device?.name || '');
const isSubmitting = ref(false);
const isForgetting = ref(false);
const errorMessage = ref('');

const hasChanged = computed(() => name.value.trim() !== (props.device?.name || ''));

async function handleSubmit() {
  if (isSubmitting.value || !props.device) return;
  isSubmitting.value = true;
  errorMessage.value = '';
  const ok = await store.renameUsbDevice(props.device.id, name.value.trim());
  isSubmitting.value = false;
  if (ok) emit('success');
  else errorMessage.value = t('musicLibrary.usb.renameError');
}

async function handleForget() {
  if (isForgetting.value || !props.device) return;
  isForgetting.value = true;
  errorMessage.value = '';
  const ok = await store.forgetUsbDevice(props.device.id);
  isForgetting.value = false;
  if (ok) emit('success');
  else errorMessage.value = t('musicLibrary.usb.forgetError');
}
</script>

<style scoped>
.usb-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.usb-form__hint {
  color: var(--color-text-secondary);
}

.usb-form__error {
  padding: var(--space-03);
  background: var(--color-error-subtle);
  border-radius: var(--radius-04);
  color: var(--color-error);
}

/* Save pinned to the bottom of the scroll area (mirrors ManageShare). */
.apply-button-sticky {
  position: sticky;
  bottom: 0;
}
</style>
