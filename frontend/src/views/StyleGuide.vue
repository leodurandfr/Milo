<!-- frontend/src/views/StyleGuide.vue -->
<template>
  <div class="style-guide">
    <header class="style-guide__header">
      <h1 class="display-1">Style Guide</h1>
      <p class="text-body text-secondary">Design System reference for Milo</p>
    </header>

    <!-- Typography -->
    <section class="style-guide__section">
      <h2 class="heading-1">Typography</h2>
      <div class="style-guide__grid">
        <div class="typography-sample">
          <span class="display-1">Display 1</span>
          <div class="typography-sample__info">
            <code class="text-mono">.display-1</code>
            <span class="text-mono text-light">32/38 · 28/32 mobile</span>
          </div>
        </div>
        <div class="typography-sample">
          <span class="heading-1">Heading 1</span>
          <div class="typography-sample__info">
            <code class="text-mono">.heading-1</code>
            <span class="text-mono text-light">26/30 · 22/26 mobile</span>
          </div>
        </div>
        <div class="typography-sample">
          <span class="heading-2">Heading 2</span>
          <div class="typography-sample__info">
            <code class="text-mono">.heading-2</code>
            <span class="text-mono text-light">22/26 · 20/24 mobile</span>
          </div>
        </div>
        <div class="typography-sample">
          <span class="heading-3">Heading 3</span>
          <div class="typography-sample__info">
            <code class="text-mono">.heading-3</code>
            <span class="text-mono text-light">20/24 · 18/22 mobile</span>
          </div>
        </div>
        <div class="typography-sample">
          <span class="heading-4">Heading 4</span>
          <div class="typography-sample__info">
            <code class="text-mono">.heading-4</code>
            <span class="text-mono text-light">18/20 · 16/18 mobile</span>
          </div>
        </div>
        <div class="typography-sample">
          <span class="text-body">Text Body</span>
          <div class="typography-sample__info">
            <code class="text-mono">.text-body</code>
            <span class="text-mono text-light">18/24 · 16/22 mobile</span>
          </div>
        </div>
        <div class="typography-sample">
          <span class="text-mono">Text Mono</span>
          <div class="typography-sample__info">
            <code class="text-mono">.text-mono</code>
            <span class="text-mono text-light">16/18 · 14/20 mobile</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Spacing -->
    <section class="style-guide__section">
      <h2 class="heading-1">Spacing</h2>
      <div class="spacing-grid">
        <div v-for="space in spacings" :key="space.name" class="spacing-sample">
          <div class="spacing-sample__bar" :style="{ width: space.value }"></div>
          <div class="spacing-sample__info">
            <code class="text-mono">{{ space.name }}</code>
            <span class="text-mono text-light">{{ space.value }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Border Radius -->
    <section class="style-guide__section">
      <h2 class="heading-1">Border Radius</h2>
      <div class="radius-grid">
        <div v-for="radius in radiuses" :key="radius.name" class="radius-sample">
          <div class="radius-sample__box" :style="{ borderRadius: radius.value }"></div>
          <div class="radius-sample__info">
            <code class="text-mono">{{ radius.name }}</code>
            <span class="text-mono text-light">{{ radius.value }}</span>
          </div>
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
  </div>
</template>

<script setup>
const spacings = [
  { name: '--space-01', value: '4px' },
  { name: '--space-02', value: '8px' },
  { name: '--space-03', value: '12px' },
  { name: '--space-04', value: '16px' },
  { name: '--space-05', value: '24px' },
  { name: '--space-06', value: '32px' },
  { name: '--space-07', value: '48px' },
  { name: '--space-08', value: '64px' },
  { name: '--space-09', value: '96px' },
]

const radiuses = [
  { name: '--radius-01', value: '4px' },
  { name: '--radius-02', value: '8px' },
  { name: '--radius-03', value: '12px' },
  { name: '--radius-04', value: '16px' },
  { name: '--radius-05', value: '20px' },
  { name: '--radius-06', value: '24px' },
  { name: '--radius-07', value: '32px' },
  { name: '--radius-08', value: '40px' },
  { name: '--radius-full', value: '9999px' },
]
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

.text-secondary {
  color: var(--color-text-secondary);
}

.text-light {
  color: var(--color-text-light);
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
  justify-content: space-between;
  padding: var(--space-03) var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-03);
}

.typography-sample__info {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--space-01);
}

.typography-sample__info code {
  color: var(--color-text-secondary);
}

/* Spacing */
.spacing-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.spacing-sample {
  display: flex;
  align-items: center;
  gap: var(--space-04);
}

.spacing-sample__bar {
  height: 24px;
  background: var(--color-brand);
  border-radius: var(--radius-01);
  min-width: 4px;
}

.spacing-sample__info {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.spacing-sample__info code {
  color: var(--color-text-secondary);
}

/* Border Radius */
.radius-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-04);
}

.radius-sample {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
}

.radius-sample__box {
  width: 64px;
  height: 64px;
  background: var(--color-brand);
}

.radius-sample__info {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.radius-sample__info code {
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

@media (max-aspect-ratio: 4/3) {
  .style-guide {
    padding: var(--space-04);
  }

  .typography-sample {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-02);
  }

  .typography-sample__info {
    align-items: flex-start;
  }

  .color-swatch {
    width: 140px;
    height: 70px;
  }

  .radius-sample__box {
    width: 48px;
    height: 48px;
  }
}
</style>
