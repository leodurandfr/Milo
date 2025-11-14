<!-- <template>
  <section class="settings-section">
    <form @submit.prevent="handleSubmit" class="change-image-form">
        <!-- Station selection (search) -->
        <div class="form-group">
          <label class="text-mono">Chercher une station favorite</label>
          <input v-model="stationName" type="text" class="form-input text-body-small"
            placeholder="Tapez pour filtrer..." @input="searchStation" @focus="showSuggestions = true" />

          <p v-if="foundStation" class="search-info success text-mono">✅ Station sélectionnée : {{ foundStation.name }}</p>
          <p v-else-if="favoriteStations.length === 0" class="search-info error text-mono">⚠️ Aucune station favorite trouvée</p>

          <!-- Favorite station suggestions -->
          <div v-if="matchingStations.length > 0 && showSuggestions" class="suggestions-list">
            <p class="suggestions-label text-mono">Stations favorites ({{ matchingStations.length }}) :</p>
            <button
              v-for="station in matchingStations"
              :key="station.id"
              type="button"
              class="suggestion-item"
              :class="{ 'selected': foundStation?.id === station.id }"
              @click="selectSuggestion(station)"
            >
              <img v-if="station.favicon" :src="station.favicon" alt="" class="suggestion-img" />
              <div v-else class="suggestion-placeholder">📻</div>
              <span class="suggestion-name text-body-small">{{ station.name }}</span>
            </button>
          </div>
        </div>

        <!-- Current image (if found) -->
        <div v-if="foundStation" class="current-image-section">
          <label class="text-mono">Image actuelle</label>
          <div class="current-image-preview">
            <img v-if="foundStation.favicon" :src="foundStation.favicon" alt="Image actuelle"
              class="current-img" />
            <div v-else class="no-image">
              <img :src="placeholderImg" alt="Station sans image" style="width: 100%; height: 100%; object-fit: cover;" />
            </div>
          </div>
        </div>

        <!-- New image -->
        <div class="form-group">
          <label class="text-mono">Nouvelle image *</label>
          <div class="image-upload-container">
            <!-- Preview -->
            <div class="image-preview" :class="{ 'has-image': imagePreview || selectedFile }">
              <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="preview-img" />
              <div v-else class="preview-placeholder">
                <span class="placeholder-icon">📷</span>
                <p class="text-mono">Cliquez pour choisir</p>
              </div>

              <!-- Remove button if an image is selected -->
              <button v-if="selectedFile" type="button" class="remove-image-btn" @click="removeImage">
                ✕
              </button>
            </div>

            <!-- Hidden file input -->
            <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif"
              @change="handleFileSelect" class="file-input" />

            <!-- Click zone -->
            <button type="button" class="upload-btn" @click="$refs.fileInput.click()">
              {{ selectedFile ? 'Changer l\'image' : 'Choisir une image' }}
            </button>

            <p class="file-info text-mono">JPG, PNG, WEBP, GIF - Max 10MB</p>
          </div>
        </div>

        <!-- Error Message -->
        <div v-if="errorMessage" class="error-message text-mono">
          ❌ {{ errorMessage }}
        </div>

        <!-- Actions -->
        <div class="form-actions">
          <Button variant="secondary" @click="$emit('back')" :disabled="isSubmitting">
            Annuler
          </Button>
          <Button variant="primary" type="submit" :disabled="isSubmitting || !foundStation || !selectedFile">
            {{ isSubmitting ? 'Modification en cours...' : 'Changer l\'image' }}
          </Button>
        </div>
      </form>
  </section>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import Button from '@/components/ui/Button.vue';
import axios from 'axios';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const emit = defineEmits(['back', 'success']);

// List of favorite stations loaded
const favoriteStations = ref([]);

// Load favorite stations when the modal is mounted
onMounted(async () => {
  try {
    const response = await axios.get('/api/radio/stations', {
      params: { favorites_only: true, limit: 10000 }
    });
    favoriteStations.value = response.data.stations;
    console.log(`✅ Chargé ${response.data.stations.length} stations favorites`);
  } catch (error) {
    console.error('❌ Erreur chargement stations favorites:', error);
    favoriteStations.value = [];
  }
});

const fileInput = ref(null);
const stationName = ref('');
const foundStation = ref(null);
const selectedFile = ref(null);
const imagePreview = ref(null);
const isSubmitting = ref(false);
const errorMessage = ref('');
const showSuggestions = ref(false);

// Favorite station suggestions while typing
const matchingStations = computed(() => {
  // If no search, show all favorite stations
  if (!stationName.value.trim()) {
    return favoriteStations.value;
  }

  // Otherwise, filter by the search term
  const query = stationName.value.toLowerCase();
  return favoriteStations.value.filter(s =>
    s.name.toLowerCase().includes(query)
  );
});

function searchStation() {
  // Only to show suggestions (filtering happens in matchingStations)
  showSuggestions.value = true;

  // Reset the selected station if the text changes
  if (foundStation.value && foundStation.value.name !== stationName.value) {
    foundStation.value = null;
  }
}

function selectSuggestion(station) {
  stationName.value = station.name;
  foundStation.value = station;
  showSuggestions.value = false; // Hide suggestions after selection
}

function handleFileSelect(event) {
  const file = event.target.files[0];

  if (!file) {
    return;
  }

  // Validate file type
  const validTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
  if (!validTypes.includes(file.type)) {
    errorMessage.value = 'Format d\'image non supporté. Utilisez JPG, PNG, WEBP ou GIF.';
    return;
  }

  // Validate file size (10MB max)
  const maxSize = 10 * 1024 * 1024; // 10MB
  if (file.size > maxSize) {
    errorMessage.value = 'Image trop volumineuse. Maximum 10MB.';
    return;
  }

  selectedFile.value = file;
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
  if (fileInput.value) {
    fileInput.value.value = '';
  }
}

async function handleSubmit() {
  if (isSubmitting.value || !foundStation.value || !selectedFile.value) return;

  errorMessage.value = '';
  isSubmitting.value = true;

  try {
    const formData = new FormData();
    formData.append('station_id', foundStation.value.id);
    formData.append('image', selectedFile.value);

    const response = await axios.post('/api/radio/custom/update-image', formData, {
      headers: {
        'Content-Type': 'multipart/form-data'
      }
    });

    if (response.data.success) {
      console.log('✅ Image modifiée avec succès');
      emit('success', response.data.station);
      emit('back');
    } else {
      errorMessage.value = response.data.error || 'Échec de la modification';
    }
  } catch (error) {
    console.error('❌ Erreur modification image:', error);
    errorMessage.value = error.response?.data?.detail || error.message || 'Une erreur est survenue';
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

.change-image-form {
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

.form-input {
  padding: var(--space-02) var(--space-03);
  border: 2px solid var(--color-background-glass);
  border-radius: var(--radius-03);
  background: var(--color-background-neutral);
  color: var(--color-text);
  transition: border-color var(--transition-fast);
}

.form-input:focus {
  outline: none;
  border-color: var(--color-brand);
}

.search-info {
  font-size: var(--font-size-small);
  margin-top: var(--space-01);
}

.search-info.success {
  color: #4caf50;
}

.search-info.error {
  color: rgb(244, 67, 54);
}

/* Suggestions */
.suggestions-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  margin-top: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background);
  border: 2px solid var(--color-background-glass);
  border-radius: var(--radius-03);
  max-height: 400px;
  overflow-y: auto;
}

.suggestions-label {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
  margin-bottom: var(--space-01);
}

.suggestion-item {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border: 2px solid transparent;
  border-radius: var(--radius-03);
  cursor: pointer;
  transition: all var(--transition-fast);
  text-align: left;
}

.suggestion-item:hover {
  background: var(--color-background);
  border-color: var(--color-brand);
  transform: translateX(4px);
}

.suggestion-item.selected {
  background: var(--color-background);
  border-color: var(--color-brand);
  box-shadow: 0 0 0 3px rgba(var(--color-brand-rgb), 0.1);
}

.suggestion-img {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-02);
  object-fit: cover;
}

.suggestion-placeholder {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.suggestion-name {
  color: var(--color-text);
  flex: 1;
}

/* Current Image */
.current-image-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.current-image-section label {
  color: var(--color-text-secondary);
}

.current-image-preview {
  width: 120px;
  height: 120px;
  border-radius: var(--radius-04);
  overflow: hidden;
  background: var(--color-background-neutral);
  border: 2px solid var(--color-background-glass);
  display: flex;
  align-items: center;
  justify-content: center;
}

.current-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.no-image {
  font-size: 48px;
  color: var(--color-text-secondary);
}

/* Image Upload */
.image-upload-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  align-items: center;
}

.image-preview {
  width: 200px;
  height: 200px;
  border-radius: var(--radius-05);
  overflow: hidden;
  background: var(--color-background-neutral);
  border: 2px dashed var(--color-background-glass);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  transition: border-color var(--transition-fast);
}

.image-preview.has-image {
  border-style: solid;
  border-color: var(--color-brand);
}

.preview-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.preview-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  color: var(--color-text-secondary);
}

.placeholder-icon {
  font-size: 48px;
}

.file-input {
  display: none;
}

.upload-btn {
  padding: var(--space-03) var(--space-05);
  background: var(--color-background-neutral);
  border: 2px solid var(--color-background-glass);
  border-radius: var(--radius-03);
  color: var(--color-text);
  font-size: var(--font-size-body);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.upload-btn:hover {
  background: var(--color-background);
  border-color: var(--color-brand);
  transform: translateY(-2px);
}

.file-info {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
}

.remove-image-btn {
  position: absolute;
  top: var(--space-02);
  right: var(--space-02);
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  background: rgba(0, 0, 0, 0.7);
  color: white;
  border: none;
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform var(--transition-fast);
  z-index: 10;
}

.remove-image-btn:hover {
  transform: scale(1.1);
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
  justify-content: flex-end;
  padding-top: var(--space-02);
}
</style> -->