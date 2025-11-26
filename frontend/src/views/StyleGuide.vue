<!-- frontend/src/views/StyleGuide.vue -->
<template>
  <div class="style-guide">
    <header class="style-guide__header">
      <h1 class="display-1">Style Guide</h1>
      <p class="text-body-small text-secondary">Design system reference for Milo</p>
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
          <span class="text-body">Text Body</span>
          <code class="text-mono">.text-body</code>
        </div>
        <div class="typography-sample">
          <span class="text-body-small">Text Body Small</span>
          <code class="text-mono">.text-body-small</code>
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
      <p class="text-body-small text-secondary">Variants: background-strong, brand, on-dark, on-light, outline, important | Props: loading, loading-label</p>

      <div class="component-grid">
        <div v-for="variant in buttonVariants" :key="variant" class="component-row">
          <h3 class="heading-2">{{ variant }}</h3>
          <div class="component-states">
            <div class="component-state">
              <Button :variant="variant">Label</Button>
              <span class="text-mono">normal</span>
            </div>
            <div class="component-state">
              <Button :variant="variant" disabled>Label</Button>
              <span class="text-mono">disabled</span>
            </div>
            <div class="component-state">
              <Button :variant="variant" loading>Label</Button>
              <span class="text-mono">loading</span>
            </div>
            <div class="component-state">
              <Button :variant="variant" left-icon="play">Label</Button>
              <span class="text-mono">with icon</span>
            </div>
            <div class="component-state">
              <Button :variant="variant" loading :loading-label="false">Label</Button>
              <span class="text-mono">loading only</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- IconButton.vue -->
    <section class="style-guide__section">
      <h2 class="heading-1">IconButton</h2>
      <p class="text-body-small text-secondary">Variants: background-strong, on-dark, on-light, rounded | Sizes: small, medium, large</p>

      <div class="component-grid">
        <div v-for="variant in iconButtonVariants" :key="variant" class="component-row">
          <h3 class="heading-2">{{ variant }}</h3>
          <div class="component-states">
            <template v-for="size in ['small', 'medium', 'large']" :key="size">
              <div class="component-state">
                <IconButton :variant="variant" :size="size" icon="play" />
                <span class="text-mono">{{ size }}</span>
              </div>
              <div class="component-state">
                <IconButton :variant="variant" :size="size" icon="play" disabled />
                <span class="text-mono">{{ size }} disabled</span>
              </div>
              <div class="component-state">
                <IconButton :variant="variant" :size="size" icon="play" loading />
                <span class="text-mono">{{ size }} loading</span>
              </div>
            </template>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';

const buttonVariants = ['background-strong', 'brand', 'on-dark', 'on-light', 'outline', 'important'];
const iconButtonVariants = ['background-strong', 'on-dark', 'on-light', 'rounded'];
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
  margin-top: var(--space-04);
  margin-bottom: var(--space-03);
  color: var(--color-text-secondary);
}

.text-secondary {
  color: var(--color-text-secondary);
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

.component-row h3 {
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

/* Dark background for on-dark and on-light variants */
.component-grid .component-row:nth-child(3),
.component-grid .component-row:nth-child(4) {
  background: linear-gradient(135deg, #2a2a2a 0%, #1a1a1a 100%);
}

.component-grid .component-row:nth-child(3) .component-state .text-mono,
.component-grid .component-row:nth-child(4) .component-state .text-mono {
  color: var(--color-text-contrast-50);
}

@media (max-aspect-ratio: 4/3) {
  .style-guide {
    padding: var(--space-04);
  }

  .color-swatch {
    width: 140px;
    height: 70px;
  }
}
</style>
