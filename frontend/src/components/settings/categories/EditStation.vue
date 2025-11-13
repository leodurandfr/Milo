<template>
  <section class="settings-section">
    <form @submit.prevent="handleSubmit" class="edit-station-form">
        <!-- Station selection (search) -->
        <div class="form-group">
          <label class="text-mono">Chercher une station à éditer</label>
          <input v-model="stationName" type="text" class="form-input text-body-small"
            placeholder="Tapez pour filtrer..." @input="searchStation" @focus="showSuggestions = true" />

          <p v-if="selectedStation" class="search-info success text-mono">✅ Station sélectionnée : {{ selectedStation.name }}</p>
          <p v-else-if="allStations.length === 0" class="search-info error text-mono">⚠️ Aucune station trouvée</p>

          <!-- Station suggestions -->
          <div v-if="matchingStations.length > 0 && showSuggestions" class="suggestions-list">
            <p class="suggestions-label text-mono">Stations ({{ matchingStations.length }}) :</p>
            <button
              v-for="station in matchingStations"
              :key="station.id"
              type="button"
              class="suggestion-item"
              :class="{ 'selected': selectedStation?.id === station.id }"
              @click="selectSuggestion(station)"
            >
              <img v-if="station.favicon" :src="station.favicon" alt="" class="suggestion-img" />
              <div v-else class="suggestion-placeholder">📻</div>
              <div class="suggestion-details">
                <span class="suggestion-name text-body-small">{{ station.name }}</span>
                <span class="suggestion-type text-mono">{{ station.id.startsWith('custom_') ? 'Station ajoutée' : 'Station favorite' }}</span>
              </div>
            </button>
          </div>
        </div>

        <!-- Edit form (appears once a station is selected) -->
        <template v-if="selectedStation">
          <!-- Image Upload Section -->
          <div class="form-group">
            <label class="text-mono">Image de la station</label>
            <div class="image-upload-container">
              <!-- Preview -->
              <div class="image-preview" :class="{ 'has-image': imagePreview || selectedFile || formData.currentImage }">
                <img v-if="imagePreview" :src="imagePreview" alt="Aperçu" class="preview-img" />
                <img v-else-if="formData.currentImage" :src="formData.currentImage" alt="Image actuelle" class="preview-img" />
                <div v-else class="preview-placeholder">
                  <img :src="placeholderImg" alt="Station sans image" class="placeholder-icon" />
                  <p class="text-mono">Cliquez pour choisir</p>
                </div>

                <!-- Remove button if new image selected or current image exists -->
                <button v-if="selectedFile || formData.currentImage" type="button" class="remove-image-btn" @click="removeImage">
                  ✕
                </button>
              </div>

              <!-- Hidden file input -->
              <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp,image/gif"
                @change="handleFileSelect" class="file-input" />

              <!-- Click zone -->
              <button type="button" class="upload-btn" @click="$refs.fileInput.click()">
                {{ selectedFile ? 'Changer l\'image' : (formData.currentImage ? 'Modifier l\'image' : 'Choisir une image') }}
              </button>

              <p class="file-info text-mono">JPG, PNG, WEBP, GIF - Max 10MB</p>
            </div>
          </div>

          <!-- Station Details -->
          <div class="form-group">
            <label class="text-mono">Nom de la station *</label>
            <input v-model="formData.name" type="text" required class="form-input text-body-small"
              placeholder="Ex: RTL" />
          </div>

          <div class="form-group">
            <label class="text-mono">URL du flux audio *</label>
            <input v-model="formData.url" type="url" required class="form-input text-body-small"
              placeholder="Ex: http://streaming.radio.fr/stream" />
          </div>

          <div class="form-row">
            <div class="form-group">
              <label class="text-mono">Pays</label>
              <select v-model="formData.country" class="form-input text-body-small">
                <option value="France">France</option>
                <option value="United Kingdom">Royaume-Uni</option>
                <option value="United States">États-Unis</option>
                <option value="Germany">Allemagne</option>
                <option value="Spain">Espagne</option>
                <option value="Italy">Italie</option>
                <option value="Belgium">Belgique</option>
                <option value="Netherlands">Pays-Bas</option>
                <option value="Switzerland">Suisse</option>
              </select>
            </div>

            <div class="form-group">
              <label class="text-mono">Genre</label>
              <select v-model="formData.genre" class="form-input text-body-small">
                <option value="Variety">Variété</option>
                <option value="Pop">Pop</option>
                <option value="Rock">Rock</option>
                <option value="Jazz">Jazz</option>
                <option value="Classical">Classique</option>
                <option value="Electronic">Electronic</option>
                <option value="News">News</option>
                <option value="Talk">Talk</option>
              </select>
            </div>
          </div>

          <!-- Info message about what will happen -->
          <div class="info-message text-mono">
            <template v-if="isCustomStation">
              ℹ️ Cette station personnalisée sera modifiée directement.
            </template>
            <template v-else>
              ℹ️ Une nouvelle station personnalisée sera créée à partir de cette station favorite.
            </template>
          </div>
        </template>

        <!-- Error Message -->
        <div v-if="errorMessage" class="error-message text-mono">
          ❌ {{ errorMessage }}
        </div>

        <!-- Actions -->
        <div class="form-actions">
          <Button variant="secondary" @click="$emit('back')" :disabled="isSubmitting">
            Annuler
          </Button>
          <Button variant="primary" type="submit" :disabled="isSubmitting || !selectedStation || !formData.name || !formData.url">
            {{ isSubmitting ? 'Modification en cours...' : 'Enregistrer' }}
          </Button>
        </div>
      </form>
  </section>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import Button from '@/components/ui/Button.vue';
import axios from 'axios';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const emit = defineEmits(['back', 'success']);

// Lists of stations loaded
const customStations = ref([]);
const favoriteStations = ref([]);

// All stations (custom + favorites)
const allStations = computed(() => [...customStations.value, ...favoriteStations.value]);

// Load stations when the modal is mounted
onMounted(async () => {
  try {
    // Load custom stations
    const customResponse = await axios.get('/api/radio/custom');
    customStations.value = customResponse.data;

    // Load favorite stations
    const favoritesResponse = await axios.get('/api/radio/stations', {
      params: { favorites_only: true, limit: 10000 }
    });
    favoriteStations.value = favoritesResponse.data.stations;

    console.log(`✅ Chargé ${customStations.value.length} stations custom et ${favoriteStations.value.length} favorites`);
  } catch (error) {
    console.error('❌ Erreur chargement stations:', error);
  }
});

const fileInput = ref(null);
const stationName = ref('');
const selectedStation = ref(null);
const selectedFile = ref(null);
const imagePreview = ref(null);
const isSubmitting = ref(false);
const errorMessage = ref('');
const showSuggestions = ref(false);

const formData = reactive({
  name: '',
  url: '',
  country: 'France',
  genre: 'Variety',
  currentImage: null, // URL of current image
  removeCurrentImage: false // Flag to remove current image
});

// Check if selected station is custom
const isCustomStation = computed(() => {
  return selectedStation.value?.id.startsWith('custom_');
});

// Station suggestions while typing
const matchingStations = computed(() => {
  // If no search, show all stations
  if (!stationName.value.trim()) {
    return allStations.value;
  }

  // Otherwise, filter by the search term
  const query = stationName.value.toLowerCase();
  return allStations.value.filter(s =>
    s.name.toLowerCase().includes(query)
  );
});

function searchStation() {
  // Only to show suggestions (filtering happens in matchingStations)
  showSuggestions.value = true;

  // Reset the selected station if the text changes
  if (selectedStation.value && selectedStation.value.name !== stationName.value) {
    selectedStation.value = null;
  }
}

function selectSuggestion(station) {
  stationName.value = station.name;
  selectedStation.value = station;
  showSuggestions.value = false; // Hide suggestions after selection

  // Populate form with station data
  formData.name = station.name;
  formData.url = station.url;
  formData.country = station.country || 'France';
  formData.genre = station.tags || station.genre || 'Variety';
  formData.currentImage = station.favicon || null;
  formData.removeCurrentImage = false;

  // Reset file selection
  selectedFile.value = null;
  imagePreview.value = null;
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
  formData.removeCurrentImage = false;
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
  formData.currentImage = null;
  formData.removeCurrentImage = true;
  if (fileInput.value) {
    fileInput.value.value = '';
  }
}

async function handleSubmit() {
  if (isSubmitting.value || !selectedStation.value) return;

  errorMessage.value = '';
  isSubmitting.value = true;

  try {
    // Prepare form data with multipart for image upload
    const submitData = new FormData();
    submitData.append('station_id', selectedStation.value.id);
    submitData.append('name', formData.name.trim());
    submitData.append('url', formData.url.trim());
    submitData.append('country', formData.country);
    submitData.append('genre', formData.genre);

    // Handle image
    if (selectedFile.value) {
      submitData.append('image', selectedFile.value);
    } else if (formData.removeCurrentImage) {
      submitData.append('remove_image', 'true');
    }

    // Different endpoint depending on whether it's a custom station or not
    let response;
    if (isCustomStation.value) {
      // Update existing custom station
      response = await axios.put('/api/radio/custom/update', submitData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
    } else {
      // Create new custom station from favorite
      response = await axios.post('/api/radio/custom/from-favorite', submitData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      });
    }

    if (response.data.success) {
      console.log('✅ Station modifiée avec succès');
      emit('success', response.data.station);
      emit('back');
    } else {
      errorMessage.value = response.data.error || 'Échec de la modification';
    }
  } catch (error) {
    console.error('❌ Erreur modification station:', error);
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

.edit-station-form {
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
  max-height: 300px;
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
  flex-shrink: 0;
}

.suggestion-placeholder {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}

.suggestion-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  flex: 1;
}

.suggestion-name {
  color: var(--color-text);
}

.suggestion-type {
  color: var(--color-text-secondary);
  font-size: var(--font-size-small);
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
  width: 80px;
  height: 80px;
  object-fit: cover;
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

/* Info Message */
.info-message {
  padding: var(--space-03);
  background: rgba(33, 150, 243, 0.1);
  border: 2px solid rgba(33, 150, 243, 0.3);
  border-radius: var(--radius-04);
  color: #2196f3;
  font-size: var(--font-size-small);
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

/* Mobile responsive */
@media (max-width: 600px) {
  .form-row {
    grid-template-columns: 1fr;
  }

  .image-preview {
    width: 150px;
    height: 150px;
  }
}
</style>
