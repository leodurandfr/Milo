<template>
  <section class="settings-section">
    <form @submit.prevent="handleSubmit" class="station-form">
      <!-- Station Name and Image Section -->
      <div class="station-header-row">
        <div class="form-group">
          <label class="text-mono">{{ $t('radio.manageStation.name') }} *</label>
          <InputText v-model="formData.name" type="text" :placeholder="$t('radio.manageStation.namePlaceholder')" />
        </div>

        <div class="image-upload-group">
          <div class="form-group">
            <label class="text-mono">{{ $t('radio.manageStation.image') }}</label>
            <Button variant="outline" size="medium" class="full-width-btn" @click="$refs.fileInput.click()">
              {{ $t('radio.manageStation.chooseImage') }}
            </Button>
          </div>
          <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif"
            @change="handleFileSelect" class="file-input" />

          <div class="favicon-preview">
            <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="favicon-img" />
            <img v-else-if="currentImageUrl" :src="currentImageUrl" alt="Image actuelle" class="favicon-img" />
            <img v-else :src="placeholderImg" alt="Station sans image" class="favicon-img" />
          </div>
        </div>

      </div>

      <div class="form-group">
        <label class="text-mono">{{ $t('radio.manageStation.url') }} *</label>
        <InputText v-model="formData.url" type="url"
          :placeholder="$t('radio.manageStation.urlPlaceholder')" />
      </div>

      <div class="form-row">
        <div class="form-group">
          <label class="text-mono">{{ $t('radio.manageStation.country') }}</label>
          <Dropdown v-model="formData.country" :options="countryOptions" :placeholder="$t('radio.manageStation.selectCountry')" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ $t('radio.manageStation.genre') }}</label>
          <InputText v-model="formData.genre" type="text"
            :placeholder="$t('radio.manageStation.genrePlaceholder')" />
        </div>
      </div>

      <div class="form-row">
        <div class="form-group">
          <label class="text-mono">{{ $t('radio.manageStation.codec') }}</label>
          <InputText v-model="formData.codec" type="text"
            :placeholder="$t('radio.manageStation.codecPlaceholder')" />
        </div>

        <div class="form-group">
          <label class="text-mono">{{ $t('radio.manageStation.bitrate') }}</label>
          <InputText v-model="formData.bitrate" type="number"
            :placeholder="$t('radio.manageStation.bitratePlaceholder')" />
        </div>
      </div>

      <!-- Error Message -->
      <div v-if="errorMessage" class="error-message text-mono">
        ❌ {{ errorMessage }}
      </div>

      <!-- Actions -->
      <div class="form-actions" :class="{ 'two-buttons': !canRestore && !canDelete }">
        <div v-if="canRestore || canDelete" class="left-actions">
          <Button v-if="canRestore" variant="important" size="medium"
            @click="handleRestoreClick" :disabled="isSubmitting">
            {{ isConfirmingRestore ? $t('common.confirm') : $t('common.restore') }}
          </Button>
          <Button v-if="canDelete" variant="important" size="medium"
            @click="handleDeleteClick" :disabled="isSubmitting">
            {{ isConfirmingDelete ? $t('radio.manageStation.confirmDelete') : $t('common.delete') }}
          </Button>
          <Button variant="background-strong" size="medium" class="cancel-btn" @click="$emit('back')" :disabled="isSubmitting">
            {{ $t('common.cancel') }}
          </Button>
        </div>
        <Button v-else variant="background-strong" size="medium" class="cancel-btn" @click="$emit('back')" :disabled="isSubmitting">
          {{ $t('common.cancel') }}
        </Button>
        <Button variant="brand" size="medium" class="save-btn" type="submit" :disabled="isSubmitting || !formData.name || !formData.url">
          {{ submitButtonText }}
        </Button>
      </div>
    </form>
  </section>
</template>

<script setup>
import { ref, reactive, onMounted, watch, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useRadioStore } from '@/stores/radioStore';
import { countryOptions as createCountryOptions } from '@/constants/countries';
import Button from '@/components/ui/Button.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import InputText from '@/components/ui/InputText.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';
import axios from 'axios';

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
  canRestore: {
    type: Boolean,
    default: false
  },
  canDelete: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['back', 'success', 'restore', 'delete']);

const { t } = useI18n();
const radioStore = useRadioStore();

const fileInput = ref(null);
const selectedFile = ref(null);
const imagePreview = ref(null);
const currentImageUrl = ref('');
const shouldRemoveImage = ref(false);
const isSubmitting = ref(false);
const errorMessage = ref('');
const isConfirmingRestore = ref(false);
const isConfirmingDelete = ref(false);
const availableCountries = ref([]);
let lastClickTime = 0;

const formData = reactive({
  name: '',
  url: '',
  country: '',
  genre: '',
  codec: '',
  bitrate: ''
});

// Computed properties
const isEditMode = computed(() => props.mode === 'edit');

const submitButtonText = computed(() => {
  if (isSubmitting.value) {
    return isEditMode.value ? t('common.saving') : t('radio.manageStation.adding');
  }
  return isEditMode.value ? t('common.save') : t('radio.manageStation.add');
});

// Load available countries from API
async function loadAvailableCountries() {
  try {
    const response = await axios.get('/api/radio/countries');
    availableCountries.value = response.data;
    console.log(`📍 Loaded ${availableCountries.value.length} countries`);
  } catch (error) {
    console.error('❌ Error loading countries:', error);
    availableCountries.value = [];
  }
}

// Convert countries to dropdown format with translations
const countryOptions = computed(() => {
  if (availableCountries.value.length === 0) {
    return [{ label: t('radio.manageStation.loading'), value: '' }];
  }
  // Use createCountryOptions helper to generate translated country names
  const translatedOptions = createCountryOptions(t, availableCountries.value, '');
  // Remove the first "All countries" option since it's not needed in station form
  return translatedOptions.slice(1);
});

// Initialize form with station data (for edit mode)
function initializeForm() {
  // Reset image-related fields
  selectedFile.value = null;
  imagePreview.value = null;
  shouldRemoveImage.value = false;
  currentImageUrl.value = '';

  if (props.station && isEditMode.value) {
    formData.name = props.station.name || '';
    formData.url = props.station.url || props.station.url_resolved || '';
    formData.country = props.station.country || '';
    formData.genre = props.station.genre || '';
    formData.codec = props.station.codec || '';
    formData.bitrate = String(props.station.bitrate || '');

    // Set current image URL if exists
    if (props.station.favicon) {
      currentImageUrl.value = props.station.favicon;
    }
  } else {
    // Reset form for add mode
    formData.name = '';
    formData.url = '';
    formData.country = '';
    formData.genre = '';
    formData.codec = '';
    formData.bitrate = '';
  }
}

// Watch for changes to station prop
watch(() => props.station, () => {
  initializeForm();
  isConfirmingRestore.value = false;
  isConfirmingDelete.value = false;
}, { immediate: true });

// Reset confirmation state if user modifies form data
watch([() => formData.name, () => formData.url, () => formData.country, () => formData.genre, selectedFile], () => {
  isConfirmingRestore.value = false;
  isConfirmingDelete.value = false;
});

onMounted(() => {
  initializeForm();
  loadAvailableCountries();
});

function handleFileSelect(event) {
  const file = event.target.files[0];

  if (!file) {
    return;
  }

  // Validate file type
  const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
  if (!validTypes.includes(file.type)) {
    errorMessage.value = t('radio.manageStation.invalidImageFormat');
    return;
  }

  // Validate file size (5MB max)
  const maxSize = 5 * 1024 * 1024; // 5MB
  if (file.size > maxSize) {
    errorMessage.value = t('radio.manageStation.imageTooLarge');
    return;
  }

  selectedFile.value = file;
  shouldRemoveImage.value = false;
  errorMessage.value = '';

  // Create preview
  const reader = new FileReader();
  reader.onload = (e) => {
    imagePreview.value = e.target.result;
  };
  reader.readAsDataURL(file);
}

function removeImage() {
  selectedFile.value = null;
  imagePreview.value = null;
  currentImageUrl.value = '';
  shouldRemoveImage.value = true;
  if (fileInput.value) {
    fileInput.value.value = '';
  }
}

function handleRestoreClick() {
  const now = Date.now();

  // Debounce: ignore clicks within 600ms of the last click
  if (now - lastClickTime < 600) {
    return;
  }

  // Update last click time
  lastClickTime = now;

  if (isConfirmingRestore.value) {
    // Second click - confirm and emit restore event
    emit('restore');
    isConfirmingRestore.value = false;
  } else {
    // First click - show confirmation state
    isConfirmingRestore.value = true;
  }
}

function handleDeleteClick() {
  const now = Date.now();

  // Debounce: ignore clicks within 600ms of the last click
  if (now - lastClickTime < 600) {
    return;
  }

  // Update last click time
  lastClickTime = now;

  if (isConfirmingDelete.value) {
    // Second click - confirm and emit delete event
    emit('delete');
    isConfirmingDelete.value = false;
  } else {
    // First click - show confirmation state
    isConfirmingDelete.value = true;
  }
}

async function handleSubmit() {
  if (isSubmitting.value) return;

  errorMessage.value = '';
  isSubmitting.value = true;

  try {
    if (isEditMode.value) {
      // Edit mode - use existing modify-metadata endpoint
      if (!props.station) {
        errorMessage.value = t('radio.manageStation.noStationToEdit');
        return;
      }

      const formDataToSend = new FormData();
      formDataToSend.append('station_id', props.station.id);
      formDataToSend.append('name', formData.name.trim());
      formDataToSend.append('url', formData.url.trim());
      formDataToSend.append('country', formData.country);
      formDataToSend.append('genre', formData.genre);
      formDataToSend.append('codec', formData.codec);
      formDataToSend.append('bitrate', parseInt(formData.bitrate, 10) || 0);
      formDataToSend.append('remove_image', shouldRemoveImage.value.toString());

      if (selectedFile.value) {
        formDataToSend.append('image', selectedFile.value);
      }

      const response = await fetch('/api/radio/favorites/modify-metadata', {
        method: 'POST',
        body: formDataToSend
      });

      const data = await response.json();

      if (data.success) {
        console.log('✅ Station modifiée avec succès:', data.station);
        emit('success', data.station);
      } else {
        errorMessage.value = data.error || t('radio.manageStation.editFailed');
      }
    } else {
      // Add mode - use radioStore.addCustomStation
      const stationData = {
        name: formData.name.trim(),
        url: formData.url.trim(),
        country: formData.country,
        genre: formData.genre,
        bitrate: parseInt(formData.bitrate, 10) || 0,
        codec: formData.codec,
        image: selectedFile.value // File object or null
      };

      const result = await radioStore.addCustomStation(stationData);

      if (result.success) {
        console.log('✅ Station ajoutée avec succès:', result.station);
        emit('success', result.station);
      } else {
        errorMessage.value = result.error || t('radio.manageStation.addFailed');
      }
    }
  } catch (error) {
    console.error('❌ Erreur station:', error);
    errorMessage.value = error.message || t('radio.manageStation.errorOccurred');
  } finally {
    isSubmitting.value = false;
  }
}
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
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

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

/* Station Header Row (Name + Image) */
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

/* Actions */
.form-actions {
  display: flex;
  gap: var(--space-03);
  padding-top: var(--space-02);
}

/* When only 2 buttons (Cancel + Save): each takes 50% */
.form-actions.two-buttons .cancel-btn,
.form-actions.two-buttons .save-btn {
  flex: 1;
}

/* When 3+ buttons: use grid for equal 50/50 split */
.form-actions:not(.two-buttons) {
  display: grid;
  grid-template-columns: 1fr 1fr;
}

/* Left actions wrapper (Restore/Delete + Cancel) */
.left-actions {
  display: flex;
  gap: var(--space-03);
}

.left-actions .btn {
  flex: 1;
}

/* Mobile responsive */
@media (max-width: 600px) {
  .form-row {
    grid-template-columns: 1fr;
  }

  .station-header-row {
    grid-template-columns: 1fr;
  }

  .favicon-preview {
    justify-content: flex-start;
  }

  .form-actions:not(.two-buttons) {
    display: flex;
    flex-direction: column;
  }

  .form-actions:not(.two-buttons) .save-btn {
    width: 100%;
  }
}
</style>
