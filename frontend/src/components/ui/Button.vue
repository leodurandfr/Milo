<!-- frontend/src/components/ui/Button.vue -->
<template>
    <button :type="type" :class="buttonClasses" :disabled="disabled" @click="handleClick">
        <LoadingSpinner v-if="loading" size="large" class="btn-icon" />
        <SvgIcon v-else-if="leftIcon" :name="leftIcon" :size="32" class="btn-icon" />
        <slot v-if="!loading || loadingLabel"></slot>
    </button>
</template>

<script>
import SvgIcon from './SvgIcon.vue'
import LoadingSpinner from './LoadingSpinner.vue'

export default {
    name: 'Button',
    components: {
        SvgIcon,
        LoadingSpinner
    },
    props: {
        variant: {
            type: String,
            default: 'background-strong',
            validator: (value) => ['background-strong', 'brand', 'on-light', 'on-dark', 'outline', 'important'].includes(value)
        },
        type: {
            type: String,
            default: 'button',
            validator: (value) => ['button', 'submit', 'reset'].includes(value)
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
        },
        loadingLabel: {
            type: Boolean,
            default: true
        }
    },
    emits: ['click'],
    computed: {
        buttonClasses() {
            const baseClasses = 'btn text-body'
            const variantClass = `btn--${this.variant}`
            const stateClass = this.getStateClass()
            const iconClass = (this.leftIcon || (this.loading && this.loadingLabel)) ? 'btn--with-icon' : ''
            const loadingOnlyClass = (this.loading && !this.loadingLabel) ? 'btn--loading-only' : ''

            return `${baseClasses} ${variantClass} ${stateClass} ${iconClass} ${loadingOnlyClass}`.trim()
        }
    },
    methods: {
        getStateClass() {
            // Loading state takes precedence - keeps variant style
            if (this.loading) {
                return 'btn--loading'
            }
            return this.disabled ? 'btn--disabled' : 'btn--normal'
        },
        handleClick(event) {
            if (!this.disabled) {
                this.$emit('click', event)
            }
        }
    }
}
</script>

<style scoped>
.btn {
    padding: var(--space-03) var(--space-04);
    text-align: center;
    border: none;
    cursor: pointer;
    transition: background-color var(--transition-fast), color var(--transition-fast), box-shadow var(--transition-fast), width var(--transition-fast);
    border-radius: var(--radius-04);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-01);
}

.btn:disabled {
    cursor: not-allowed;
}

/* Button with left icon - padding spécifique */
.btn--with-icon {
    padding: var(--space-02) var(--space-04) var(--space-02) var(--space-02);
}

/* === BACKGROUND-STRONG variant === */
.btn--background-strong.btn--normal {
    background-color: var(--color-background-strong);
    color: var(--color-text-secondary);
}

.btn--background-strong.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === BRAND variant === */
.btn--brand.btn--normal {
    background-color: var(--color-brand);
    color: var(--color-text-contrast);
}

.btn--brand.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === ON-LIGHT variant (dark button for light backgrounds) === */
.btn--on-light.btn--normal {
    background-color: var(--color-background-contrast-12);
    color: var(--color-text-contrast);
}

.btn--on-light.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === ON-DARK variant (light button for dark backgrounds) === */
.btn--on-dark.btn--normal {
    background-color: var(--color-background-neutral-12);
    color: var(--color-text-contrast);
}

.btn--on-dark.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === OUTLINE variant === */
.btn--outline.btn--normal {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

.btn--outline.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
    box-shadow: none;
}

/* === IMPORTANT variant === */
.btn--important.btn--normal {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

.btn--important.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === LOADING state - preserves variant styling === */
.btn--loading {
    cursor: wait;
    pointer-events: none;
}

.btn--background-strong.btn--loading {
    background-color: var(--color-background-strong);
    color: var(--color-text-secondary);
}

.btn--brand.btn--loading {
    background-color: var(--color-brand);
    color: var(--color-text-contrast);
}

.btn--on-light.btn--loading {
    background-color: var(--color-background-contrast-12);
    color: var(--color-text-contrast);
}

.btn--on-dark.btn--loading {
    background-color: var(--color-background-neutral-12);
    color: var(--color-text-contrast);
}

.btn--outline.btn--loading {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

.btn--important.btn--loading {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

/* === LOADING ONLY (spinner centered, no label) === */
.btn--loading-only {
    padding: var(--space-02);
}

/* === RESPONSIVE (Mobile) === */
@media (max-aspect-ratio: 4/3) {
    .btn .btn-icon :deep(svg) {
        width: 24px !important;
        height: 24px !important;
    }

    .btn :deep(.loading-spinner) {
        --spinner-size: 24px !important;
    }
}
</style>