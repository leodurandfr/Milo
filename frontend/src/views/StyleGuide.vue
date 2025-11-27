<!-- frontend/src/views/StyleGuide.vue -->
<template>
  <div class="style-guide">
    <header class="style-guide__header">
      <h1 class="display-1">Style Guide</h1>
      <p class="text-body text-secondary">Design system reference for Milo</p>
    </header>

    <!-- Typography -->
    <section class="style-guide__section">
      <h2 class="heading-1">Typography</h2>
      <div class="style-guide__grid">
        <div class="typography-sample">
          <span class="display-1">Display 1</span>
          <code class="text-mono">.display-1</code>
        </div>
        <div class="typography-sample">
          <span class="heading-1">Heading 1</span>
          <code class="text-mono">.heading-1</code>
        </div>
        <div class="typography-sample">
          <span class="heading-2">Heading 2</span>
          <code class="text-mono">.heading-2</code>
        </div>
        <div class="typography-sample">
          <span class="heading-3">Heading 3</span>
          <code class="text-mono">.heading-3</code>
        </div>
        <div class="typography-sample">
          <span class="heading-4">Heading 4</span>
          <code class="text-mono">.heading-4</code>
        </div>
        <div class="typography-sample">
          <span class="text-body">Text Body</span>
          <code class="text-mono">.text-body</code>
        </div>
        <div class="typography-sample">
          <span class="text-mono">Text Mono</span>
          <code class="text-mono">.text-mono</code>
        </div>
      </div>
    </section>

    <!-- Colors -->
    <section class="style-guide__section">
      <h2 class="heading-1">Colors</h2>

      <h3 class="heading-2">Brand</h3>
      <div class="color-grid">
        <div class="color-swatch" style="background: var(--color-brand)">
          <span class="color-swatch__label">--color-brand</span>
        </div>
      </div>

      <h3 class="heading-2">Text</h3>
      <div class="color-grid">
        <div class="color-swatch" style="background: var(--color-text)">
          <span class="color-swatch__label">--color-text</span>
        </div>
        <div class="color-swatch" style="background: var(--color-text-secondary)">
          <span class="color-swatch__label">--color-text-secondary</span>
        </div>
        <div class="color-swatch" style="background: var(--color-text-light)">
          <span class="color-swatch__label">--color-text-light</span>
        </div>
        <div class="color-swatch" style="background: var(--color-text-contrast)">
          <span class="color-swatch__label color-swatch__label--dark">--color-text-contrast</span>
        </div>
      </div>

      <h3 class="heading-2">Background</h3>
      <div class="color-grid">
        <div class="color-swatch color-swatch--bordered" style="background: var(--color-background)">
          <span class="color-swatch__label color-swatch__label--dark">--color-background</span>
        </div>
        <div class="color-swatch color-swatch--bordered" style="background: var(--color-background-strong)">
          <span class="color-swatch__label color-swatch__label--dark">--color-background-strong</span>
        </div>
        <div class="color-swatch color-swatch--bordered" style="background: var(--color-background-neutral)">
          <span class="color-swatch__label color-swatch__label--dark">--color-background-neutral</span>
        </div>
        <div class="color-swatch" style="background: var(--color-background-contrast)">
          <span class="color-swatch__label">--color-background-contrast</span>
        </div>
      </div>

      <h3 class="heading-2">System</h3>
      <div class="color-grid">
        <div class="color-swatch" style="background: var(--color-success)">
          <span class="color-swatch__label">--color-success</span>
        </div>
        <div class="color-swatch" style="background: var(--color-warning)">
          <span class="color-swatch__label color-swatch__label--dark">--color-warning</span>
        </div>
        <div class="color-swatch" style="background: var(--color-error)">
          <span class="color-swatch__label">--color-error</span>
        </div>
        <div class="color-swatch" style="background: var(--color-info)">
          <span class="color-swatch__label">--color-info</span>
        </div>
      </div>
    </section>

    <!-- Button.vue -->
    <section class="style-guide__section">
      <h2 class="heading-1">Button</h2>
      <p class="text-mono text-secondary">Variants: background-strong, brand, on-dark, on-light, outline, important | Sizes: medium, small</p>

      <div class="controls-panel">
        <label class="control-item">
          <span class="text-mono">variant</span>
          <select v-model="buttonState.variant">
            <option v-for="v in buttonVariants" :key="v" :value="v">{{ v }}</option>
          </select>
        </label>
        <label class="control-item">
          <span class="text-mono">size</span>
          <select v-model="buttonState.size">
            <option value="medium">medium</option>
            <option value="small">small</option>
          </select>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="buttonState.disabled" />
          <span class="text-mono">disabled</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="buttonState.loading" />
          <span class="text-mono">loading</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="buttonState.loadingLabel" />
          <span class="text-mono">loadingLabel</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="buttonState.hasIcon" />
          <span class="text-mono">leftIcon</span>
        </label>
      </div>

      <div class="preview-container" :class="buttonPreviewBg">
        <Button
          :variant="buttonState.variant"
          :size="buttonState.size"
          :disabled="buttonState.disabled"
          :loading="buttonState.loading"
          :loading-label="buttonState.loadingLabel"
          :left-icon="buttonState.hasIcon ? 'play' : null"
        >
          Label
        </Button>
      </div>

      <!-- All variants overview -->
      <h3 class="heading-3">All Variants</h3>
      <div class="component-grid">
        <div v-for="variant in buttonVariants" :key="variant" :class="['component-row', `component-row--${variant}`]">
          <h4 class="heading-4">{{ variant }}</h4>
          <div class="component-states">
            <div class="component-state">
              <Button :variant="variant" size="medium">Label</Button>
              <span class="text-mono">medium</span>
            </div>
            <div class="component-state">
              <Button :variant="variant" size="small">Label</Button>
              <span class="text-mono">small</span>
            </div>
            <div class="component-state">
              <Button :variant="variant" size="medium" left-icon="play">Label</Button>
              <span class="text-mono">icon</span>
            </div>
            <div class="component-state">
              <Button :variant="variant" size="medium" disabled>Label</Button>
              <span class="text-mono">disabled</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- IconButton.vue -->
    <section class="style-guide__section">
      <h2 class="heading-1">IconButton</h2>
      <p class="text-mono text-secondary">Variants: background-strong, on-dark, on-light, rounded | Sizes: small, medium, large</p>

      <div class="controls-panel">
        <label class="control-item">
          <span class="text-mono">variant</span>
          <select v-model="iconButtonState.variant">
            <option v-for="v in iconButtonVariants" :key="v" :value="v">{{ v }}</option>
          </select>
        </label>
        <label class="control-item">
          <span class="text-mono">size</span>
          <select v-model="iconButtonState.size">
            <option value="small">small</option>
            <option value="medium">medium</option>
            <option value="large">large</option>
          </select>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="iconButtonState.disabled" />
          <span class="text-mono">disabled</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="iconButtonState.loading" />
          <span class="text-mono">loading</span>
        </label>
      </div>

      <div class="preview-container" :class="iconButtonPreviewBg">
        <IconButton
          :variant="iconButtonState.variant"
          :size="iconButtonState.size"
          :disabled="iconButtonState.disabled"
          :loading="iconButtonState.loading"
          icon="play"
        />
      </div>

      <!-- All variants overview -->
      <h3 class="heading-3">All Variants</h3>
      <div class="component-grid">
        <div v-for="variant in iconButtonVariants" :key="variant" :class="['component-row', `component-row--${variant}`]">
          <h4 class="heading-4">{{ variant }}</h4>
          <div class="component-states">
            <div v-for="size in ['small', 'medium', 'large']" :key="size" class="component-state">
              <IconButton :variant="variant" :size="size" icon="play" />
              <span class="text-mono">{{ size }}</span>
            </div>
            <div class="component-state">
              <IconButton :variant="variant" size="medium" icon="play" disabled />
              <span class="text-mono">disabled</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'

const buttonVariants = ['background-strong', 'brand', 'on-dark', 'on-light', 'outline', 'important']
const iconButtonVariants = ['background-strong', 'on-dark', 'on-light', 'rounded']

// Button interactive state
const buttonState = ref({
  variant: 'background-strong',
  size: 'medium',
  disabled: false,
  loading: false,
  loadingLabel: true,
  hasIcon: false
})

// IconButton interactive state
const iconButtonState = ref({
  variant: 'background-strong',
  size: 'medium',
  disabled: false,
  loading: false
})

// Compute preview background based on variant
const buttonPreviewBg = computed(() => {
  if (buttonState.value.variant === 'on-dark') return 'preview-bg--dark'
  if (buttonState.value.variant === 'on-light') return 'preview-bg--light'
  return ''
})

const iconButtonPreviewBg = computed(() => {
  if (iconButtonState.value.variant === 'on-dark') return 'preview-bg--dark'
  if (iconButtonState.value.variant === 'on-light' || iconButtonState.value.variant === 'rounded') return 'preview-bg--light'
  return ''
})
</script>

<style scoped>
.style-guide {
  padding: var(--space-06);
  max-width: 1200px;
  margin: 0 auto;
  background: var(--color-background);
  min-height: 100vh;
}

.style-guide__header {
  margin-bottom: var(--space-07);
}

.style-guide__section {
  margin-bottom: var(--space-07);
}

.style-guide__section h2 {
  margin-bottom: var(--space-04);
  color: var(--color-text);
}

.style-guide__section h3 {
  margin-top: var(--space-05);
  margin-bottom: var(--space-03);
  color: var(--color-text-secondary);
}

.style-guide__section > p {
  margin-bottom: var(--space-04);
}

.text-secondary {
  color: var(--color-text-secondary);
}

/* Controls Panel */
.controls-panel {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-03);
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  margin-bottom: var(--space-04);
}

.control-item {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  cursor: pointer;
}

.control-item input[type="checkbox"] {
  width: 18px;
  height: 18px;
  cursor: pointer;
}

.control-item select {
  padding: var(--space-02);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
  background: var(--color-background);
  color: var(--color-text);
  font-family: 'Space Mono Regular';
  font-size: var(--font-size-mono);
}

/* Preview Container */
.preview-container {
  padding: var(--space-05);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 80px;
  margin-bottom: var(--space-04);
}

.preview-container.preview-bg--dark {
  background: var(--color-background-contrast);
}

.preview-container.preview-bg--light {
  background: #858585;
}

/* Typography */
.style-guide__grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.typography-sample {
  display: flex;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-03);
}

.typography-sample code {
  color: var(--color-text-secondary);
}

/* Colors */
.color-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-03);
}

.color-swatch {
  width: 160px;
  height: 80px;
  border-radius: var(--radius-03);
  display: flex;
  align-items: flex-end;
  padding: var(--space-02);
}

.color-swatch--bordered {
  box-shadow: inset 0 0 0 1px var(--color-border);
}

.color-swatch__label {
  font-size: 11px;
  font-family: 'Space Mono Regular';
  color: var(--color-text-contrast);
  background: rgba(0, 0, 0, 0.3);
  padding: 2px 6px;
  border-radius: 4px;
}

.color-swatch__label--dark {
  color: var(--color-text);
  background: rgba(255, 255, 255, 0.7);
}

/* Components */
.component-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.component-row {
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.component-row h4 {
  margin-top: 0;
  margin-bottom: var(--space-03);
}

.component-states {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-04);
  align-items: flex-start;
}

.component-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
}

.component-state .text-mono {
  font-size: 11px;
  color: var(--color-text-light);
}

/* Dark background for on-dark variant */
.component-row--on-dark {
  background: var(--color-background-contrast);
}

.component-row--on-dark .component-state .text-mono {
  color: var(--color-text-contrast-50);
}

/* Strong background for on-light and rounded variants */
.component-row--on-light,
.component-row--rounded {
  background: #858585;
}

@media (max-aspect-ratio: 4/3) {
  .style-guide {
    padding: var(--space-04);
  }

  .color-swatch {
    width: 140px;
    height: 70px;
  }

  .controls-panel {
    flex-direction: column;
    gap: var(--space-02);
  }
}
</style>
