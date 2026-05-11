<template>
  <div class="manage-station-view">
    <!-- Restore/Delete confirm drawer (mirrors the home power-menu pattern,
         lives inside view-content like power-menu does). The outer region is
         always rendered so the close animation plays when the user navigates
         away with the drawer open. -->
    <div class="station-action-menu-region"
      :class="{ 'station-action-menu-region--open': showActionMenu && actionIcon }">
      <div class="station-action-menu-items">
        <ListItemButton v-if="actionIcon" @click="$emit('confirm-action')">
          <template #icon>
            <SvgIcon :name="actionIcon" :size="40" />
          </template>
          <template #title>
            {{ actionIcon === 'arrowCounterClockwise'
                ? t('radio.manageStation.confirmRestore')
                : t('radio.manageStation.confirmDelete') }}
          </template>
        </ListItemButton>
      </div>
    </div>

  <SettingsSection>
    <form @submit.prevent="handleFormSubmit" class="station-form">
      <!-- Station Name and Image Section (horizontal on desktop, stacked on mobile) -->
      <div class="station-header-row">
        <div class="form-group">
          <label class="text-mono">{{ t('radio.manageStation.name') }} *</label>
          <InputText v-model="formData.name" type="text" :placeholder="t('radio.manageStation.namePlaceholder')" />
        </div>

        <div class="image-upload-group">
          <div class="form-group">
            <label class="text-mono">{{ t('radio.manageStation.image') }}</label>
            <Button variant="outline" size="medium" class="full-width-btn" @click="$refs.fileInput.click()">
              {{ t('radio.manageStation.chooseImage') }}
            </Button>
          </div>
          <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif"
            @change="handleFileSelect" class="file-input" />

          <div class="favicon-preview">
            <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="favicon-img" />
            <img v-else-if="currentImageUrl" :src="currentImageUrl" alt="Image actuelle" class="favicon-img" />
            <img v-else :src="generateStationAvatar(formData.name || 'Radio')" alt="Station sans image" class="favicon-img" />
          </div>
        </div>
      </div>

      <div class="form-group">
        <label class="text-mono">{{ t('radio.manageStation.url') }} *</label>
        <InputText v-model="formData.url" type="url"
          :placeholder="t('radio.manageStation.urlPlaceholder')" />
      </div>

      <!-- Country + Genre (horizontal on desktop, stacked on mobile) -->
      <div class="form-row">
        <div class="form-group">
          <label class="text-mono">{{ t('radio.manageStation.country') }}</label>
          <Dropdown v-model="formData.country" :options="countryOptions" :placeholder="t('radio.manageStation.selectCountry')" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ t('radio.manageStation.genre') }}</label>
          <InputText v-model="formData.genre" type="text"
            :placeholder="t('radio.manageStation.genrePlaceholder')" />
        </div>
      </div>

      <!-- Codec + Bitrate (horizontal on desktop, stacked on mobile) -->
      <div class="form-row">
        <div class="form-group">
          <label class="text-mono">{{ t('radio.manageStation.codec') }}</label>
          <InputText v-model="formData.codec" type="text"
            :placeholder="t('radio.manageStation.codecPlaceholder')" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ t('radio.manageStation.bitrate') }}</label>
          <InputText v-model="formData.bitrate" type="number"
            :placeholder="t('radio.manageStation.bitratePlaceholder')" />
        </div>
      </div>

      <!-- Shazam per-station toggle -->
      <ListItemButton
        class="shazam-toggle"
        :title="t('radio.manageStation.shazamEnabled')"
        variant="background"
        action="toggle"
        :model-value="formData.shazam_enabled"
        :disabled="!globalShazamEnabled"
        @update:model-value="handleShazamToggle"
      />

      <!-- Error Message -->
      <div v-if="errorMessage" class="error-message text-mono">
        ❌ {{ errorMessage }}
      </div>

      <!-- Add mode: explicit "Create station" button. Edit mode auto-saves via watchers. -->
      <Button v-if="!isEditMode" variant="brand" size="medium" class="create-btn" type="submit"
        :disabled="isSubmitting || !formData.name || !formData.url">
        {{ submitButtonText }}
      </Button>
    </form>
  </SettingsSection>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, watch, computed, nextTick } from 'vue';
import { useI18n } from '@/services/i18n';
import { logger } from '@/services/logger';
import { useRadioStore } from '@/stores/radioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { countryOptions as createCountryOptions } from '@/constants/countries';
import Button from '@/components/ui/Button.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { generateStationAvatar } from '@/utils/stationAvatar';
import axios from 'axios';
import SettingsSection from '@/components/settings/SettingsSection.vue';

const props = defineProps({
  mode: {
    type: String,
    default: 'add',
    validator: v => ['add', 'edit'].includes(v)
  },
  station: {
    type: Object,
    default: null
  },
  showActionMenu: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['back', 'success', 'confirm-action']);

// Icon for the Restore/Delete confirm drawer. Mirrors the IconButton in the
// parent's NavigationHeader actions slot.
const actionIcon = computed(() => {
  if (!props.station) return null;
  if (props.station._canRestore) return 'arrowCounterClockwise';
  if (props.station._canDelete) return 'trash';
  return null;
});

const { t } = useI18n();
const radioStore = useRadioStore();
const settingsStore = useSettingsStore();

// Per-station toggle is meaningless when global track recognition is OFF.
// We keep it visible (disabled) so users discover the feature and the value
// is persisted in advance.
const globalShazamEnabled = computed(() => settingsStore.radioSettings.shazam_enabled);

const fileInput = ref(null);
const selectedFile = ref(null);
const imagePreview = ref(null);
const currentImageUrl = ref('');
const isSubmitting = ref(false);
const errorMessage = ref('');
const availableCountries = ref([]);

const formData = reactive({
  name: '',
  url: '',
  country: '',
  genre: '',
  codec: '',
  bitrate: '',
  shazam_enabled: true
});

const isEditMode = computed(() => props.mode === 'edit');

const submitButtonText = computed(() => {
  if (isSubmitting.value) return t('radio.manageStation.adding');
  return t('radio.manageStation.createStation');
});

// === Country options ===

async function loadAvailableCountries() {
  try {
    const response = await axios.get('/api/radio/countries');
    availableCountries.value = response.data;
    logger.debug('radio', `Loaded ${availableCountries.value.length} countries`);
  } catch (error) {
    logger.error('radio', 'Error loading countries:', error);
    availableCountries.value = [];
  }
}

const countryOptions = computed(() => {
  if (availableCountries.value.length === 0) {
    return [{ label: t('radio.manageStation.loading'), value: '' }];
  }
  const translatedOptions = createCountryOptions(t, availableCountries.value, '');
  return translatedOptions.slice(1);
});

// === Form initialization ===

function initializeForm() {
  selectedFile.value = null;
  imagePreview.value = null;
  currentImageUrl.value = '';

  if (props.station && isEditMode.value) {
    formData.name = props.station.name || '';
    formData.url = props.station.url || props.station.url_resolved || '';
    formData.country = props.station.country || '';
    formData.genre = props.station.genre || '';
    formData.codec = props.station.codec || '';
    formData.bitrate = String(props.station.bitrate || '');
    formData.shazam_enabled = props.station.shazam_enabled !== false;

    if (props.station.favicon) {
      currentImageUrl.value = props.station.favicon;
    }
  } else {
    formData.name = '';
    formData.url = '';
    formData.country = '';
    formData.genre = '';
    formData.codec = '';
    formData.bitrate = '';
    formData.shazam_enabled = true;
  }
}

// === Auto-save (edit mode) ===

// Flag that gates auto-save: true once the form is populated from props.station.
// Prevents the initial population from firing a no-op save.
const isInitialized = ref(false);
const isSaving = ref(false);
const pendingSave = ref(false);
const SAVE_DEBOUNCE_MS = 500;
let saveDebounceTimer = null;

function clearDebounce() {
  if (saveDebounceTimer) {
    clearTimeout(saveDebounceTimer);
    saveDebounceTimer = null;
  }
}

function triggerSave({ instant = false } = {}) {
  if (!isEditMode.value || !isInitialized.value) return;
  clearDebounce();
  if (instant) {
    saveEdit();
  } else {
    saveDebounceTimer = setTimeout(() => {
      saveDebounceTimer = null;
      saveEdit();
    }, SAVE_DEBOUNCE_MS);
  }
}

async function saveEdit() {
  if (!props.station) return;
  // Validation guard: skip save when required fields are empty.
  // The user sees no save happens; typing valid content resumes auto-save.
  if (!formData.name.trim() || !formData.url.trim()) return;

  if (isSaving.value) {
    pendingSave.value = true;
    return;
  }
  isSaving.value = true;
  errorMessage.value = '';

  try {
    const formDataToSend = new FormData();
    formDataToSend.append('station_id', props.station.id);
    formDataToSend.append('name', formData.name.trim());
    formDataToSend.append('url', formData.url.trim());
    formDataToSend.append('country', formData.country);
    formDataToSend.append('genre', formData.genre);
    formDataToSend.append('codec', formData.codec);
    formDataToSend.append('bitrate', parseInt(formData.bitrate, 10) || 0);
    formDataToSend.append('remove_image', 'false');
    formDataToSend.append('shazam_enabled', formData.shazam_enabled.toString());
    if (selectedFile.value) {
      formDataToSend.append('image', selectedFile.value);
    }

    const { data } = await axios.post('/api/radio/favorites/modify-metadata', formDataToSend);

    if (data.success) {
      // After an image upload, swap the local preview for the server-side URL
      // so subsequent saves don't re-upload the same file.
      if (selectedFile.value && data.station?.favicon) {
        currentImageUrl.value = data.station.favicon;
        selectedFile.value = null;
        imagePreview.value = null;
      }
    } else {
      errorMessage.value = data.error || t('radio.manageStation.editFailed');
    }
  } catch (error) {
    logger.error('radio', 'Auto-save failed:', error);
    errorMessage.value = error?.response?.data?.detail || error.message || t('radio.manageStation.errorOccurred');
  } finally {
    isSaving.value = false;
    if (pendingSave.value) {
      pendingSave.value = false;
      // Re-fire with latest state — covers changes that arrived during the in-flight save.
      saveEdit();
    }
  }
}

// Text inputs: debounced 500ms after last keystroke
watch([
  () => formData.name,
  () => formData.url,
  () => formData.genre,
  () => formData.codec,
  () => formData.bitrate,
], () => triggerSave());

// Toggle / dropdown / file: instant
watch([
  () => formData.country,
  () => formData.shazam_enabled,
], () => triggerSave({ instant: true }));

function handleShazamToggle(value) {
  formData.shazam_enabled = value;
}

// === Watchers ===

watch(() => props.station, async () => {
  isInitialized.value = false;
  initializeForm();
  // Wait until form watchers have observed the initial values without firing.
  await nextTick();
  isInitialized.value = true;
}, { immediate: true });

onMounted(() => {
  loadAvailableCountries();
});

onUnmounted(() => {
  clearDebounce();
});

// === File selection ===

function handleFileSelect(event) {
  const file = event.target.files[0];
  if (!file) return;

  const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
  if (!validTypes.includes(file.type)) {
    errorMessage.value = t('radio.manageStation.invalidImageFormat');
    return;
  }

  const maxSize = 5 * 1024 * 1024;
  if (file.size > maxSize) {
    errorMessage.value = t('radio.manageStation.imageTooLarge');
    return;
  }

  selectedFile.value = file;
  errorMessage.value = '';

  const reader = new FileReader();
  reader.onload = (e) => {
    imagePreview.value = e.target.result;
  };
  reader.readAsDataURL(file);

  // File pick is an explicit user action — flush the save without debouncing.
  triggerSave({ instant: true });
}

// === Form submit ===
// Edit mode: Enter inside an input force-flushes the pending debounced save.
// Add mode: Enter or click on the "Create station" button creates the station.

async function handleFormSubmit() {
  if (isEditMode.value) {
    if (saveDebounceTimer) {
      clearDebounce();
      await saveEdit();
    }
    return;
  }
  await handleAddSubmit();
}

async function handleAddSubmit() {
  if (isSubmitting.value) return;
  if (!formData.name.trim() || !formData.url.trim()) return;

  errorMessage.value = '';
  isSubmitting.value = true;

  try {
    const stationData = {
      name: formData.name.trim(),
      url: formData.url.trim(),
      country: formData.country,
      genre: formData.genre,
      bitrate: parseInt(formData.bitrate, 10) || 0,
      codec: formData.codec,
      image: selectedFile.value,
      shazam_enabled: formData.shazam_enabled
    };

    const result = await radioStore.addCustomStation(stationData);

    if (result.success) {
      logger.info('radio', 'Station added successfully', result.station);
      emit('success', result.station);
    } else {
      errorMessage.value = result.error || t('radio.manageStation.addFailed');
    }
  } catch (error) {
    logger.error('radio', 'Error submitting station form:', error);
    errorMessage.value = error.message || t('radio.manageStation.errorOccurred');
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<style scoped>
/* Override the default view-content gap (--space-02) so the closed drawer
 * doesn't add phantom space between header and form. Mirrors the .home-view
 * trick that lets the power-menu collapse without contributing layout space.
 * The chained selector has higher specificity than the parent's .view-content
 * rule so the override wins regardless of stylesheet load order. */
.view-content.manage-station-view {
  gap: 0;
}

/* Restore/Delete confirm drawer — same animation as the home power-menu. */
.station-action-menu-region {
  max-height: 0;
  overflow: visible;
  transition: max-height var(--transition-fast);
}

.station-action-menu-region--open {
  max-height: 70px;
}

.station-action-menu-items {
  opacity: 0;
  transform: translateY(-100%);
  transition: opacity var(--transition-medium), transform var(--transition-medium);
}

.station-action-menu-region--open .station-action-menu-items {
  opacity: 1;
  transform: translateY(0);
  padding-bottom: var(--space-02);
}

.station-action-menu-items :deep(.list-item-button) {
  background: var(--color-background-neutral-50);
}

.station-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
}

.shazam-toggle {
  margin-top: var(--space-04);
  margin-bottom: var(--space-02);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

/* Name + Image side-by-side on desktop. */
.station-header-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
  align-items: start;
}

.image-upload-group {
  display: flex;
  gap: var(--space-03);
  justify-content: space-between;
}

.image-upload-group .form-group {
  flex: 1;
}

.full-width-btn {
  width: 100%;
}

.favicon-preview {
  display: flex;
  align-items: flex-start;
  justify-content: center;
}

.favicon-img {
  width: 76px;
  height: 76px;
  object-fit: cover;
  border-radius: var(--radius-03);
  background: var(--color-background-strong);
}

.file-input {
  display: none;
}

/* Error Message */
.error-message {
  padding: var(--space-03);
  background: rgba(244, 67, 54, 0.1);
  border: 2px solid rgba(244, 67, 54, 0.3);
  border-radius: var(--radius-04);
  color: rgb(244, 67, 54);
}

.create-btn {
  margin-top: var(--space-02);
}

/* Mobile: stack all paired rows vertically for readability */
@media (max-aspect-ratio: 4/3) {
  .form-row,
  .station-header-row {
    grid-template-columns: 1fr;
  }

  .favicon-preview {
    justify-content: flex-start;
  }
}
</style>
