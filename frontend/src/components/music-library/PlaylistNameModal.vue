<template>
  <Modal :is-open="isOpen" @close="$emit('close')">
    <div class="playlist-name-modal">
      <NavigationHeader :title="title" />

      <SettingsSection>
        <div class="playlist-name-modal__field">
          <InputText
            v-model="name"
            :placeholder="placeholder || t('musicLibrary.playlists.namePlaceholder')"
            :maxlength="255"
            @submit="handleSubmit"
          />
          <Button variant="brand" :loading="loading" :disabled="loading || !name.trim()" @click="handleSubmit">
            {{ submitLabel }}
          </Button>
        </div>
      </SettingsSection>
    </div>
  </Modal>
</template>

<script setup>
import { ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import Modal from '@/components/ui/Modal.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    default: false,
  },
  title: {
    type: String,
    required: true,
  },
  submitLabel: {
    type: String,
    required: true,
  },
  initialName: {
    type: String,
    default: '',
  },
  placeholder: {
    type: String,
    default: '',
  },
  loading: {
    type: Boolean,
    default: false,
  },
});

const emit = defineEmits(['close', 'submit']);

const { t } = useI18n();
const name = ref('');

watch(() => props.isOpen, (open) => {
  if (open) name.value = props.initialName;
}, { immediate: true });

function handleSubmit() {
  const trimmed = name.value.trim();
  if (!trimmed || props.loading) return;
  emit('submit', trimmed);
}
</script>

<style scoped>
.playlist-name-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.playlist-name-modal__field {
  display: flex;
  flex-direction: row;
  gap: var(--space-02);
  align-items: center;
}
</style>
