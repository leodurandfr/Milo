<!-- frontend/src/components/dsp/PresetSaveDialog.vue -->
<!-- Dialog for saving DSP presets with name input -->
<template>
  <Transition name="dialog-fade">
    <div v-if="isOpen" class="preset-save-dialog">
      <div class="dialog-overlay" @click="handleClose"></div>
      <div class="dialog-content">
        <h3 class="heading-3">{{ $t('dsp.savePreset', 'Save Preset') }}</h3>

        <InputText
          ref="inputRef"
          v-model="localName"
          :placeholder="$t('dsp.presetName', 'Preset name')"
          :maxlength="32"
          @keyup.enter="handleSave"
        />

        <div class="dialog-actions">
          <Button variant="background-strong" @click="handleClose">
            {{ $t('common.cancel', 'Cancel') }}
          </Button>
          <Button
            variant="brand"
            :loading="saving"
            :disabled="!localName.trim()"
            @click="handleSave"
          >
            {{ $t('common.save', 'Save') }}
          </Button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';

const props = defineProps({
  isOpen: {
    type: Boolean,
    default: false
  },
  initialName: {
    type: String,
    default: ''
  },
  saving: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['close', 'save']);

const localName = ref('');
const inputRef = ref(null);

// Sync initial name when dialog opens
watch(() => props.isOpen, (open) => {
  if (open) {
    localName.value = props.initialName;
    // Focus input after dialog opens
    nextTick(() => {
      inputRef.value?.focus?.();
    });
  }
});

function handleClose() {
  if (!props.saving) {
    emit('close');
  }
}

function handleSave() {
  const name = localName.value.trim();
  if (name && !props.saving) {
    emit('save', name);
  }
}
</script>

<style scoped>
.preset-save-dialog {
  position: fixed;
  inset: 0;
  z-index: 9000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.dialog-overlay {
  position: absolute;
  inset: 0;
  background: var(--color-background-medium-32);
  backdrop-filter: blur(4px);
}

.dialog-content {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  padding: var(--space-05);
  background: var(--color-background-elevated);
  border-radius: var(--radius-06);
  box-shadow: var(--shadow-02);
  min-width: 280px;
  max-width: 90vw;
}

.dialog-content h3 {
  margin: 0;
  color: var(--color-text);
}

.dialog-actions {
  display: flex;
  gap: var(--space-02);
  justify-content: flex-end;
}

/* Transition animations */
.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity var(--transition-normal);
}

.dialog-fade-enter-active .dialog-content,
.dialog-fade-leave-active .dialog-content {
  transition: transform var(--transition-normal), opacity var(--transition-normal);
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;
}

.dialog-fade-enter-from .dialog-content,
.dialog-fade-leave-to .dialog-content {
  transform: scale(0.95);
  opacity: 0;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .dialog-content {
    min-width: 260px;
    padding: var(--space-04);
  }
}
</style>
