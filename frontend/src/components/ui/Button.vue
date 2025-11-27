<!-- frontend/src/components/ui/Button.vue -->
<template>
    <button :type="type" :class="buttonClasses" :disabled="disabled" @click="handleClick">
        <LoadingSpinner v-if="loading" size="inherit" class="btn-icon" />
        <SvgIcon v-else-if="leftIcon" :name="leftIcon" class="btn-icon" />
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
            validator: (value) => ['background-strong', 'brand', 'on-grey', 'on-dark', 'outline', 'important'].includes(value)
        },
        size: {
            type: String,
            default: 'medium',
            validator: (value) => ['medium', 'small'].includes(value)
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
            const typoClass = this.size === 'small' ? 'heading-4' : 'heading-3'
            const baseClasses = `btn ${typoClass}`
            const variantClass = `btn--${this.variant}`
            const sizeClass = `btn--${this.size}`
            const stateClass = this.getStateClass()
            const iconClass = (this.leftIcon || (this.loading && this.loadingLabel)) ? 'btn--with-icon' : ''
            const loadingOnlyClass = (this.loading && !this.loadingLabel) ? 'btn--loading-only' : ''

            return `${baseClasses} ${variantClass} ${sizeClass} ${stateClass} ${iconClass} ${loadingOnlyClass}`.trim()
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
    text-align: center;
    border: none;
    cursor: pointer;
    transition: background-color var(--transition-fast), color var(--transition-fast), box-shadow var(--transition-fast);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
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
    padding: 12px 16px 12px 12px;
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
}

/* Icon sizes per button size */
.btn--medium .btn-icon :deep(svg) {
    width: 24px;
    height: 24px;
}

.btn--medium :deep(.loading-spinner) {
    --spinner-size: 24px;
}

.btn--small .btn-icon :deep(svg) {
    width: 20px;
    height: 20px;
}

.btn--small :deep(.loading-spinner) {
    --spinner-size: 20px;
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

/* === on-grey variant (dark button for light backgrounds) === */
.btn--on-grey.btn--normal {
    background-color: var(--color-background-contrast-12);
    color: var(--color-text-contrast);
}

.btn--on-grey.btn--disabled {
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
    color: var(--color-error);
    box-shadow: inset 0 0 0 2px var(--color-error);
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

.btn--on-grey.btn--loading {
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
    color: var(--color-error);
    box-shadow: inset 0 0 0 2px var(--color-error);
}

/* === LOADING ONLY (spinner centered, no label) === */
.btn--loading-only {
    padding: var(--space-02);
}

/* === RESPONSIVE (Mobile) === */
@media (max-aspect-ratio: 4/3) {
    .btn--medium {
        height: 38px;
        padding: 8px 16px;
        border-radius: var(--radius-03);
    }

    .btn--medium.btn--with-icon {
        padding: 8px 12px 8px 8px;
    }

    .btn--medium .btn-icon :deep(svg) {
        width: 22px;
        height: 22px;
    }

    .btn--medium :deep(.loading-spinner) {
        --spinner-size: 22px;
    }

    .btn--small {
        height: 34px;
    }

    .btn--small .btn-icon :deep(svg) {
        width: 18px;
        height: 18px;
    }

    .btn--small :deep(.loading-spinner) {
        --spinner-size: 18px;
    }
}
</style>