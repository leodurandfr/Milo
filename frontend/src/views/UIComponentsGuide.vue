<!-- frontend/src/views/UIComponentsGuide.vue -->
<template>
  <div class="ui-components-guide">
    <header class="style-guide__header">
      <h1 class="display-1">UI Components</h1>
      <p class="text-body text-secondary">Interactive component reference for Milo design system</p>
    </header>

    <!-- Navigation -->
    <nav class="component-nav">
      <a href="#buttons" class="text-mono">Buttons</a>
      <a href="#forms" class="text-mono">Forms</a>
      <a href="#sliders" class="text-mono">Sliders</a>
      <a href="#feedback" class="text-mono">Feedback</a>
    </nav>

    <!-- ==================== BUTTONS ==================== -->
    <section id="buttons" class="style-guide__section">
      <h2 class="heading-1">Buttons</h2>

      <!-- Button -->
      <div class="component-block">
        <h3 class="heading-2">Button</h3>
        <p class="text-mono text-secondary">Variants: background-strong, brand, on-dark, on-grey, outline, important | Sizes: medium, small</p>

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
        <h4 class="heading-3">All Variants</h4>
        <div class="component-grid">
          <div v-for="variant in buttonVariants" :key="variant" :class="['component-row', `component-row--${variant}`]">
            <h5 class="heading-4">{{ variant }}</h5>
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
                <span class="text-mono">medium + icon</span>
              </div>
              <div class="component-state">
                <Button :variant="variant" size="small" left-icon="play">Label</Button>
                <span class="text-mono">small + icon</span>
              </div>
              <div class="component-state">
                <Button :variant="variant" size="medium" disabled>Label</Button>
                <span class="text-mono">disabled</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- IconButton -->
      <div class="component-block">
        <h3 class="heading-2">IconButton</h3>
        <p class="text-mono text-secondary">Variants: background-strong, on-dark, on-grey, rounded | Sizes: small, medium, large</p>

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
        <h4 class="heading-3">All Variants</h4>
        <div class="component-grid">
          <div v-for="variant in iconButtonVariants" :key="variant" :class="['component-row', `component-row--${variant}`]">
            <h5 class="heading-4">{{ variant }}</h5>
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
      </div>
    </section>

    <!-- ==================== FORMS ==================== -->
    <section id="forms" class="style-guide__section">
      <h2 class="heading-1">Forms</h2>

      <!-- InputText -->
      <div class="component-block">
        <h3 class="heading-2">InputText</h3>
        <p class="text-mono text-secondary">Types: text, password, email, number | Props: icon, maxlength</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">type</span>
            <select v-model="inputState.type">
              <option value="text">text</option>
              <option value="password">password</option>
              <option value="email">email</option>
              <option value="number">number</option>
            </select>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="inputState.disabled" />
            <span class="text-mono">disabled</span>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="inputState.hasIcon" />
            <span class="text-mono">icon</span>
          </label>
        </div>

        <div class="preview-container">
          <div class="preview-wrapper input-width">
            <InputText
              v-model="inputState.value"
              :type="inputState.type"
              :disabled="inputState.disabled"
              :icon="inputState.hasIcon ? 'search' : ''"
              placeholder="Enter text..."
            />
          </div>
        </div>
      </div>

      <!-- Dropdown -->
      <div class="component-block">
        <h3 class="heading-2">Dropdown</h3>
        <p class="text-mono text-secondary">Variants: default, minimal</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">variant</span>
            <select v-model="dropdownState.variant">
              <option value="default">default</option>
              <option value="minimal">minimal</option>
            </select>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="dropdownState.disabled" />
            <span class="text-mono">disabled</span>
          </label>
        </div>

        <div class="preview-container">
          <div class="preview-wrapper input-width">
            <Dropdown
              v-model="dropdownState.value"
              :options="dropdownOptions"
              :variant="dropdownState.variant"
              :disabled="dropdownState.disabled"
              placeholder="Select option"
            />
          </div>
        </div>
      </div>

      <!-- Toggle -->
      <div class="component-block">
        <h3 class="heading-2">Toggle</h3>
        <p class="text-mono text-secondary">Variants: primary, secondary | Sizes: default, compact</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">variant</span>
            <select v-model="toggleState.variant">
              <option value="primary">primary</option>
              <option value="secondary">secondary</option>
            </select>
          </label>
          <label class="control-item">
            <span class="text-mono">size</span>
            <select v-model="toggleState.size">
              <option value="default">default</option>
              <option value="compact">compact</option>
            </select>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="toggleState.disabled" />
            <span class="text-mono">disabled</span>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="toggleState.hasTitle" />
            <span class="text-mono">title</span>
          </label>
        </div>

        <div class="preview-container">
          <Toggle
            v-model="toggleState.value"
            :variant="toggleState.variant"
            :size="toggleState.size"
            :disabled="toggleState.disabled"
            :title="toggleState.hasTitle ? 'Toggle label' : ''"
          />
        </div>
      </div>
    </section>

    <!-- ==================== SLIDERS ==================== -->
    <section id="sliders" class="style-guide__section">
      <h2 class="heading-1">Sliders</h2>

      <!-- RangeSlider -->
      <div class="component-block">
        <h3 class="heading-2">RangeSlider</h3>
        <p class="text-mono text-secondary">Orientations: horizontal, vertical | Props: min, max, step, valueUnit, hideInlineValue</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">orientation</span>
            <select v-model="rangeState.orientation">
              <option value="horizontal">horizontal</option>
              <option value="vertical">vertical</option>
            </select>
          </label>
          <label class="control-item">
            <span class="text-mono">min</span>
            <input type="number" v-model.number="rangeState.min" />
          </label>
          <label class="control-item">
            <span class="text-mono">max</span>
            <input type="number" v-model.number="rangeState.max" />
          </label>
          <label class="control-item">
            <span class="text-mono">step</span>
            <input type="number" v-model.number="rangeState.step" min="1" />
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="rangeState.disabled" />
            <span class="text-mono">disabled</span>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="rangeState.hideInlineValue" />
            <span class="text-mono">hideInlineValue</span>
          </label>
        </div>

        <div class="preview-container" :class="{ 'preview-container--vertical': rangeState.orientation === 'vertical' }">
          <div class="preview-wrapper" :class="rangeState.orientation === 'vertical' ? 'slider-vertical' : 'slider-horizontal'">
            <RangeSlider
              v-model="rangeState.value"
              :min="rangeState.min"
              :max="rangeState.max"
              :step="rangeState.step"
              :orientation="rangeState.orientation"
              :disabled="rangeState.disabled"
              :hide-inline-value="rangeState.hideInlineValue"
              value-unit="%"
            />
          </div>
        </div>
      </div>

      <!-- DoubleRangeSlider -->
      <div class="component-block">
        <h3 class="heading-2">DoubleRangeSlider</h3>
        <p class="text-mono text-secondary">Props: min, max, step, gap, valueUnit</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">min</span>
            <input type="number" v-model.number="doubleRangeState.min" />
          </label>
          <label class="control-item">
            <span class="text-mono">max</span>
            <input type="number" v-model.number="doubleRangeState.max" />
          </label>
          <label class="control-item">
            <span class="text-mono">step</span>
            <input type="number" v-model.number="doubleRangeState.step" min="1" />
          </label>
          <label class="control-item">
            <span class="text-mono">gap</span>
            <input type="number" v-model.number="doubleRangeState.gap" />
          </label>
        </div>

        <div class="preview-container">
          <div class="preview-wrapper slider-horizontal">
            <DoubleRangeSlider
              v-model="doubleRangeState.value"
              :min="doubleRangeState.min"
              :max="doubleRangeState.max"
              :step="doubleRangeState.step"
              :gap="doubleRangeState.gap"
              value-unit="Hz"
            />
          </div>
        </div>
      </div>
    </section>

    <!-- ==================== FEEDBACK ==================== -->
    <section id="feedback" class="style-guide__section">
      <h2 class="heading-1">Lists & Feedback</h2>

      <!-- ListItemButton -->
      <div class="component-block">
        <h3 class="heading-2">ListItemButton</h3>
        <p class="text-mono text-secondary">Variants: default, active, inactive | Actions: none, caret, toggle</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">variant</span>
            <select v-model="listItemState.variant">
              <option value="default">default</option>
              <option value="active">active</option>
              <option value="inactive">inactive</option>
            </select>
          </label>
          <label class="control-item">
            <span class="text-mono">action</span>
            <select v-model="listItemState.action">
              <option value="none">none</option>
              <option value="caret">caret</option>
              <option value="toggle">toggle</option>
            </select>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="listItemState.hasIcon" />
            <span class="text-mono">icon</span>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="listItemState.disabled" />
            <span class="text-mono">disabled</span>
          </label>
        </div>

        <div class="preview-container">
          <div class="preview-wrapper row-width">
            <ListItemButton
              title="List item title"
              :variant="listItemState.variant"
              :action="listItemState.action"
              :model-value="listItemState.toggleValue"
              :disabled="listItemState.disabled"
              @update:model-value="listItemState.toggleValue = $event"
            >
              <template v-if="listItemState.hasIcon" #icon>
                <SvgIcon name="settings" :size="40" />
              </template>
            </ListItemButton>
          </div>
        </div>

        <!-- All variants overview -->
        <h4 class="heading-3">All Variants</h4>
        <div class="component-grid">
          <div class="component-row">
            <h5 class="heading-4">default (navigation)</h5>
            <div class="component-states component-states--vertical">
              <ListItemButton title="With caret" action="caret">
                <template #icon><SvgIcon name="settings" :size="40" /></template>
              </ListItemButton>
              <ListItemButton title="Without action">
                <template #icon><SvgIcon name="settings" :size="40" /></template>
              </ListItemButton>
            </div>
          </div>
          <div class="component-row">
            <h5 class="heading-4">active / inactive (selection)</h5>
            <div class="component-states component-states--vertical">
              <ListItemButton title="Active item" variant="active">
                <template #icon><SvgIcon name="settings" :size="40" /></template>
              </ListItemButton>
              <ListItemButton title="Inactive item" variant="inactive">
                <template #icon><SvgIcon name="settings" :size="40" /></template>
              </ListItemButton>
            </div>
          </div>
          <div class="component-row">
            <h5 class="heading-4">toggle action</h5>
            <div class="component-states component-states--vertical">
              <ListItemButton title="Toggle on" variant="active" action="toggle" :model-value="true">
                <template #icon><SvgIcon name="settings" :size="40" /></template>
              </ListItemButton>
              <ListItemButton title="Toggle off" variant="inactive" action="toggle" :model-value="false">
                <template #icon><SvgIcon name="settings" :size="40" /></template>
              </ListItemButton>
              <ListItemButton title="Disabled" variant="inactive" action="toggle" :model-value="false" disabled>
                <template #icon><SvgIcon name="settings" :size="40" /></template>
              </ListItemButton>
            </div>
          </div>
        </div>
      </div>

      <!-- LoadingSpinner -->
      <div class="component-block">
        <h3 class="heading-2">LoadingSpinner</h3>
        <p class="text-mono text-secondary">Sizes: small, medium, large (or number) | Variants: default, background</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">size</span>
            <select v-model="spinnerState.size">
              <option value="small">small</option>
              <option value="medium">medium</option>
              <option value="large">large</option>
            </select>
          </label>
          <label class="control-item">
            <span class="text-mono">variant</span>
            <select v-model="spinnerState.variant">
              <option value="default">default</option>
              <option value="background">background</option>
            </select>
          </label>
        </div>

        <div class="preview-container">
          <LoadingSpinner :size="spinnerState.size" :variant="spinnerState.variant" />
        </div>
      </div>

      <!-- MessageContent -->
      <div class="component-block">
        <h3 class="heading-2">MessageContent</h3>
        <p class="text-mono text-secondary">Props: loading | Slot-only: no predefined text/icon fields</p>

        <div class="controls-panel">
          <label class="control-item">
            <input type="checkbox" v-model="messageState.loading" />
            <span class="text-mono">loading</span>
          </label>
        </div>

        <div class="preview-container">
          <div class="preview-wrapper row-width">
            <MessageContent :loading="messageState.loading">
              <p class="text-body text-secondary">Custom message content goes here</p>
            </MessageContent>
          </div>
        </div>
      </div>

      <!-- ModalHeader -->
      <div class="component-block">
        <h3 class="heading-2">ModalHeader</h3>
        <p class="text-mono text-secondary">Variants: contrast, background-neutral | Props: title, subtitle, showBack, icon, actionsKey</p>

        <div class="controls-panel">
          <label class="control-item">
            <span class="text-mono">variant</span>
            <select v-model="modalHeaderState.variant">
              <option value="contrast">contrast</option>
              <option value="background-neutral">background-neutral</option>
            </select>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="modalHeaderState.showBack" />
            <span class="text-mono">showBack</span>
          </label>
          <label class="control-item">
            <input type="checkbox" :checked="!!modalHeaderState.subtitle" @change="modalHeaderState.subtitle = $event.target.checked ? 'Subtitle' : ''" />
            <span class="text-mono">subtitle</span>
          </label>
          <label class="control-item">
            <input type="checkbox" :checked="!!modalHeaderState.icon" @change="modalHeaderState.icon = $event.target.checked ? 'radio' : ''" />
            <span class="text-mono">icon</span>
          </label>
          <label class="control-item">
            <input type="checkbox" v-model="modalHeaderState.hasActions" />
            <span class="text-mono">actions slot</span>
          </label>
        </div>

        <div class="preview-container">
          <div class="preview-wrapper row-width">
            <ModalHeader
              :title="modalHeaderState.title"
              :subtitle="modalHeaderState.subtitle || null"
              :show-back="modalHeaderState.showBack"
              :variant="modalHeaderState.variant"
              :icon="modalHeaderState.icon || null"
              @back="() => {}"
            >
              <template v-if="modalHeaderState.hasActions" #actions="{ iconVariant }">
                <IconButton icon="settings" :variant="iconVariant" />
              </template>
            </ModalHeader>
          </div>
        </div>

        <!-- All variants overview -->
        <h4 class="heading-3">All Variants</h4>
        <div class="component-grid">
          <div class="component-row">
            <h5 class="heading-4">contrast (default)</h5>
            <div class="component-states component-states--vertical">
              <ModalHeader title="Simple Title" variant="contrast" />
              <ModalHeader title="With Subtitle" subtitle="Subtitle" variant="contrast" />
              <ModalHeader title="With Back" variant="contrast" :show-back="true" @back="() => {}" />
              <ModalHeader title="Back + Subtitle" subtitle="Subtitle" variant="contrast" :show-back="true" @back="() => {}" />
              <ModalHeader title="With Icon" variant="contrast" icon="radio" />
              <ModalHeader title="Icon + Subtitle" subtitle="Subtitle" variant="contrast" icon="radio" />
            </div>
          </div>
          <div class="component-row">
            <h5 class="heading-4">background-neutral</h5>
            <div class="component-states component-states--vertical">
              <ModalHeader title="Simple Title" variant="background-neutral" />
              <ModalHeader title="With Subtitle" subtitle="Subtitle" variant="background-neutral" />
              <ModalHeader title="With Back" variant="background-neutral" :show-back="true" @back="() => {}" />
              <ModalHeader title="Back + Subtitle" subtitle="Subtitle" variant="background-neutral" :show-back="true" @back="() => {}" />
              <ModalHeader title="With Icon" variant="background-neutral" icon="radio" />
              <ModalHeader title="Icon + Subtitle" subtitle="Subtitle" variant="background-neutral" icon="radio" />
            </div>
          </div>
          <div class="component-row">
            <h5 class="heading-4">with actions slot</h5>
            <div class="component-states component-states--vertical">
              <ModalHeader title="With Actions" variant="contrast">
                <template #actions="{ iconVariant }">
                  <IconButton icon="settings" :variant="iconVariant" />
                </template>
              </ModalHeader>
              <ModalHeader title="Back + Actions" variant="contrast" :show-back="true" @back="() => {}">
                <template #actions="{ iconVariant }">
                  <IconButton icon="close" :variant="iconVariant" />
                </template>
              </ModalHeader>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

// UI Components
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import InputText from '@/components/ui/InputText.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import Toggle from '@/components/ui/Toggle.vue'
import RangeSlider from '@/components/ui/RangeSlider.vue'
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue'
import ListItemButton from '@/components/ui/ListItemButton.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import MessageContent from '@/components/ui/MessageContent.vue'
import ModalHeader from '@/components/ui/ModalHeader.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'

// Variants
const buttonVariants = ['background-strong', 'brand', 'on-dark', 'on-grey', 'outline', 'important']
const iconButtonVariants = ['background-strong', 'on-dark', 'on-grey', 'rounded']

// Button state
const buttonState = ref({
  variant: 'background-strong',
  size: 'medium',
  disabled: false,
  loading: false,
  loadingLabel: true,
  hasIcon: false
})

// IconButton state
const iconButtonState = ref({
  variant: 'background-strong',
  size: 'medium',
  disabled: false,
  loading: false
})

// InputText state
const inputState = ref({
  value: '',
  type: 'text',
  disabled: false,
  hasIcon: false
})

// Dropdown state
const dropdownState = ref({
  value: '',
  variant: 'default',
  disabled: false
})

const dropdownOptions = [
  { label: 'Option 1', value: 'opt1' },
  { label: 'Option 2', value: 'opt2' },
  { label: 'Option 3', value: 'opt3' }
]

// Toggle state
const toggleState = ref({
  value: false,
  variant: 'primary',
  size: 'default',
  disabled: false,
  hasTitle: false
})

// RangeSlider state
const rangeState = ref({
  value: 50,
  min: 0,
  max: 100,
  step: 1,
  orientation: 'horizontal',
  disabled: false,
  hideInlineValue: false
})

// DoubleRangeSlider state
const doubleRangeState = ref({
  value: { min: 20, max: 80 },
  min: 0,
  max: 100,
  step: 1,
  gap: 10
})

// ListItemButton state
const listItemState = ref({
  variant: 'default',
  action: 'caret',
  hasIcon: true,
  disabled: false,
  toggleValue: false
})

// LoadingSpinner state
const spinnerState = ref({
  size: 'medium',
  variant: 'default'
})

// MessageContent state
const messageState = ref({
  loading: false
})

// ModalHeader state
const modalHeaderState = ref({
  title: 'Modal Title',
  subtitle: '',
  showBack: false,
  variant: 'contrast',
  icon: '',
  hasActions: false
})

// Computed preview backgrounds
const buttonPreviewBg = computed(() => {
  if (buttonState.value.variant === 'on-dark') return 'preview-bg--dark'
  if (buttonState.value.variant === 'on-grey') return 'preview-bg--light'
  return ''
})

const iconButtonPreviewBg = computed(() => {
  if (iconButtonState.value.variant === 'on-dark') return 'preview-bg--dark'
  if (iconButtonState.value.variant === 'on-grey' || iconButtonState.value.variant === 'rounded') return 'preview-bg--light'
  return ''
})
</script>

<style scoped>
.ui-components-guide {
  padding: var(--space-06);
  max-width: 1200px;
  margin: 0 auto;
  background: var(--color-background);
  min-height: 100vh;
}

.style-guide__header {
  margin-bottom: var(--space-05);
}

/* Navigation */
.component-nav {
  display: flex;
  gap: var(--space-04);
  padding: var(--space-03) var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  margin-bottom: var(--space-06);
}

.component-nav a {
  color: var(--color-text-secondary);
  text-decoration: none;
  transition: color var(--transition-fast);
}

.component-nav a:hover {
  color: var(--color-brand);
}

/* Sections */
.style-guide__section {
  margin-bottom: var(--space-07);
}

.style-guide__section > h2 {
  margin-bottom: var(--space-04);
  color: var(--color-text);
}

/* Component blocks */
.component-block {
  margin-bottom: var(--space-06);
  padding-bottom: var(--space-06);
  border-bottom: 1px solid var(--color-border);
}

.component-block:last-child {
  border-bottom: none;
}

.component-block h3 {
  margin-bottom: var(--space-02);
}

.component-block > p {
  margin-bottom: var(--space-04);
}

.component-block h4 {
  margin-top: var(--space-05);
  margin-bottom: var(--space-03);
  color: var(--color-text-secondary);
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

.control-item select,
.control-item input[type="number"] {
  padding: var(--space-02);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
  background: var(--color-background);
  color: var(--color-text);
  font-family: 'Space Mono Regular';
  font-size: var(--font-size-mono);
}

.control-item input[type="number"] {
  width: 70px;
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
}

.preview-container--vertical {
  min-height: 200px;
}

.preview-container.preview-bg--dark {
  background: var(--color-background-contrast);
}

.preview-container.preview-bg--light {
  background: #858585;
}

/* Preview wrappers */
.preview-wrapper {
  width: 100%;
}

.preview-wrapper.input-width {
  max-width: 300px;
}

.preview-wrapper.row-width {
  max-width: 500px;
}

.preview-wrapper.slider-horizontal {
  max-width: 400px;
}

.preview-wrapper.slider-vertical {
  height: 150px;
  width: 60px;
}

/* Component grid for all variants */
.component-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.component-row {
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

.component-row h5 {
  margin-bottom: var(--space-03);
}

.component-states {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-04);
  align-items: flex-start;
}

.component-states--vertical {
  flex-direction: column;
  gap: var(--space-02);
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

/* Light background for on-grey and rounded variants */
.component-row--on-grey,
.component-row--rounded {
  background: #858585;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .ui-components-guide {
    padding: var(--space-04);
  }

  .component-nav {
    flex-wrap: wrap;
    gap: var(--space-02);
  }

  .controls-panel {
    flex-direction: column;
    gap: var(--space-02);
  }

  .preview-wrapper.row-width {
    max-width: 100%;
  }

  .preview-wrapper.slider-horizontal {
    max-width: 100%;
  }
}
</style>
