<!-- frontend/src/components/ui/Button.vue -->
<template>
    <button v-press :type="type" :class="buttonClasses" :disabled="disabled" @click="handleClick">
        <LoadingSpinner v-if="loading" size="inherit" class="btn-icon" />
        <SvgIcon v-else-if="leftIcon" :name="leftIcon" class="btn-icon" />
        <slot></slot>
    </button>
</template>

<script setup>
import { computed } from 'vue'
import SvgIcon from './SvgIcon.vue'
import LoadingSpinner from './LoadingSpinner.vue'

const props = defineProps({
    variant: {
        type: String,
        default: 'control',
        validator: (value) => ['control', 'surface', 'brand', 'on-contrast', 'outline', 'outline-neutral', 'important'].includes(value)
    },
    size: {
        type: String,
        default: 'medium',
        validator: (value) => ['medium', 'small'].includes(value)
    },
    // 'submit' only for a button inside a <form @submit.prevent>; the default
    // 'button' is what stops any other button from submitting its ancestor form.
    type: {
        type: String,
        default: 'button',
        validator: (value) => ['button', 'submit'].includes(value)
    },
    disabled: {
        type: Boolean,
        default: false
    },
    leftIcon: {
        type: String,
        default: null
    },
    loading: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['click'])

function getStateClass() {
    // Loading + disabled = disabled appearance with spinner
    if (props.loading && props.disabled) {
        return 'btn--disabled'
    }
    // Loading alone keeps variant style
    if (props.loading) {
        return 'btn--loading'
    }
    return props.disabled ? 'btn--disabled' : 'btn--normal'
}

const buttonClasses = computed(() => {
    const typoClass = props.size === 'small' ? 'heading-4' : 'heading-3'
    const baseClasses = `btn ${typoClass}`
    const variantClass = `btn--${props.variant}`
    const sizeClass = `btn--${props.size}`
    const stateClass = getStateClass()
    const iconClass = (props.leftIcon || props.loading) ? 'btn--with-icon' : ''

    return `${baseClasses} ${variantClass} ${sizeClass} ${stateClass} ${iconClass}`.trim()
})

function handleClick(event) {
    if (!props.disabled) {
        emit('click', event)
    }
}
</script>

<style scoped>
.btn {
    text-align: center;
    border: none;
    cursor: pointer;
    transition: background-color var(--transition-fast), color var(--transition-fast), box-shadow var(--transition-fast), var(--transition-press);
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.btn:disabled {
    cursor: not-allowed;
}

/* === SIZE variants === */
/* Medium (default): 48px raspberry / 38px mobile */
.btn--medium {
    padding: 12px 16px;
    border-radius: var(--radius-04);
}

.btn--medium.btn--with-icon {
    padding: 10px 16px 10px 10px;
    gap: 8px;
}

/* Small: 36px raspberry / 34px mobile */
.btn--small {
    height: 36px;
    padding: 8px 12px;
    border-radius: var(--radius-03);
}

.btn--small.btn--with-icon {
    padding: 8px 12px 8px 8px;
    gap: 6px;

}

/* Icon sizes per button size */
.btn--medium .btn-icon :deep(svg) {
    width: 28px;
    height: 28px;
}

.btn--medium :deep(.loading-spinner) {
    --spinner-size: 28px;
}

.btn--small .btn-icon :deep(svg) {
    width: 24px;
    height: 24px;
}

.btn--small :deep(.loading-spinner) {
    --spinner-size: 24px;
}

/* === CONTROL variant === */
.btn--control.btn--normal {
    background-color: var(--color-control);
    color: var(--color-text);
}

.btn--control.btn--disabled {
    background-color: var(--color-inset);
    color: var(--color-text-tertiary);
}

/* === SURFACE variant (the panel's fill, no border) === */
.btn--surface.btn--normal {
    background-color: var(--color-surface);
    color: var(--color-text-secondary);
}

.btn--surface.btn--disabled {
    background-color: var(--color-inset);
    color: var(--color-text-tertiary);
}

/* === BRAND variant === */
.btn--brand.btn--normal {
    background-color: var(--color-brand);
    color: var(--color-text-on-brand);
}

.btn--brand.btn--disabled {
    background-color: var(--color-inset);
    color: var(--color-text-tertiary);
}

/* === ON-CONTRAST variant (a glint on a contrast surface) === */
.btn--on-contrast.btn--normal {
    background-color: var(--color-glint);
    color: var(--color-text-on-contrast);
}

.btn--on-contrast.btn--disabled {
    background-color: var(--color-glint);
    color: var(--color-text-on-contrast-secondary);
}

/* === OUTLINE variant === */
.btn--outline.btn--normal {
    background-color: var(--color-panel);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

.btn--outline.btn--disabled {
    background-color: var(--color-inset);
    color: var(--color-text-tertiary);
    box-shadow: none;
}

/* === OUTLINE-NEUTRAL variant (neutral border, e.g. unselected ButtonGroup item) === */
.btn--outline-neutral.btn--normal {
    background-color: var(--color-panel);
    color: var(--color-text-secondary);
    box-shadow: inset 0 0 0 2px var(--color-border);
}

.btn--outline-neutral.btn--disabled {
    background-color: var(--color-inset);
    color: var(--color-text-tertiary);
    box-shadow: none;
}

/* === IMPORTANT variant (a destructive action: a red fill, no border) === */
.btn--important.btn--normal {
    background-color: var(--color-error);
    color: var(--color-text-on-error);
}

.btn--important.btn--disabled {
    background-color: var(--color-inset);
    color: var(--color-text-tertiary);
}

/* === LOADING state - preserves variant styling === */
.btn--loading {
    cursor: wait;
    pointer-events: none;
}

.btn--control.btn--loading {
    background-color: var(--color-control);
    color: var(--color-text);
}

.btn--surface.btn--loading {
    background-color: var(--color-surface);
    color: var(--color-text-secondary);
}

.btn--brand.btn--loading {
    background-color: var(--color-brand);
    color: var(--color-text-on-brand);
}

.btn--on-contrast.btn--loading {
    background-color: var(--color-glint);
    color: var(--color-text-on-contrast);
}

.btn--outline.btn--loading {
    background-color: var(--color-panel);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

.btn--outline-neutral.btn--loading {
    background-color: var(--color-panel);
    color: var(--color-text-secondary);
    box-shadow: inset 0 0 0 2px var(--color-border);
}

.btn--important.btn--loading {
    background-color: var(--color-error);
    color: var(--color-text-on-error);
}

/* === RESPONSIVE (Mobile) === */
@media (max-aspect-ratio: 4/3) {
    .btn--medium {
        height: 38px;
        padding: 8px 16px;
        border-radius: var(--radius-03);
    }

    .btn--medium.btn--with-icon {
        padding: 6px 12px 6px 6px;
    }

    .btn--medium .btn-icon :deep(svg) {
        width: 24px;
        height: 24px;
    }

    .btn--medium :deep(.loading-spinner) {
        --spinner-size: 24px;
    }

    .btn--small {
        height: 34px;
    }

    .btn--small .btn-icon :deep(svg) {
        width: 20px;
        height: 20px;
    }

    .btn--small :deep(.loading-spinner) {
        --spinner-size: 20px;
    }
}
</style>