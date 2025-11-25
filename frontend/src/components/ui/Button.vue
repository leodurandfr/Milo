<!-- frontend/src/components/ui/Button.vue -->
<template>
    <button :type="type" :class="buttonClasses" :disabled="disabled" @click="handleClick">
        <LoadingSpinner v-if="loading" :size="32" />
        <SvgIcon v-else-if="leftIcon" :name="leftIcon" :size="32" />
        <slot v-if="!loading"></slot>
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
            default: 'default',
            validator: (value) => ['default', 'primary', 'dark', 'outline', 'important', 'toggle'].includes(value)
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
        active: {
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
        size: {
            type: String,
            default: 'default',
            validator: (value) => ['default', 'small'].includes(value)
        }
    },
    emits: ['click'],
    computed: {
        buttonClasses() {
            const textClass = this.size === 'small' ? 'text-body-small' : 'text-body'
            const baseClasses = `btn ${textClass}`
            const variantClass = `btn--${this.variant}`
            const stateClass = this.getStateClass()
            const iconClass = (this.leftIcon || this.loading) ? 'btn--with-icon' : ''

            return `${baseClasses} ${variantClass} ${stateClass} ${iconClass}`.trim()
        }
    },
    methods: {
        getStateClass() {
            if (this.variant === 'toggle') {
                return this.active ? 'btn--active' : 'btn--inactive'
            }
            // Loading state takes precedence - keeps variant style
            if (this.loading) {
                return 'btn--loading'
            }
            return this.disabled ? 'btn--disabled' : 'btn--default'
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

/* === DEFAULT variant === */
.btn--default.btn--default {
    background-color: var(--color-background-strong);
    color: var(--color-text-secondary);
}

.btn--default.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === PRIMARY variant === */
.btn--primary.btn--default {
    background-color: var(--color-brand);
    color: var(--color-text-contrast);
}

.btn--primary.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === DARK variant === */
.btn--dark.btn--default {
    background-color: var(--color-background-contrast-12);
    color: var(--color-text-contrast);
}

.btn--dark.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === OUTLINE variant === */
.btn--outline.btn--default {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: 0 0 0 2px var(--color-brand);
}

.btn--outline.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
    box-shadow: none;
}

/* === IMPORTANT variant === */
.btn--important.btn--default {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

.btn--important.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* === TOGGLE variant === */
.btn--toggle.btn--active {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}

.btn--toggle.btn--inactive {
    background-color: var(--color-background-strong);
    color: var(--color-text-secondary);
}

/* === LOADING state - preserves variant styling === */
.btn--loading {
    cursor: wait;
    pointer-events: none;
}

.btn--default.btn--loading {
    background-color: var(--color-background-strong);
    color: var(--color-text-secondary);
}

.btn--primary.btn--loading {
    background-color: var(--color-brand);
    color: var(--color-text-contrast);
}

.btn--dark.btn--loading {
    background-color: var(--color-background-contrast-12);
    color: var(--color-text-contrast);
}

.btn--outline.btn--loading {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: 0 0 0 2px var(--color-brand);
}

.btn--important.btn--loading {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    box-shadow: inset 0 0 0 2px var(--color-brand);
}
</style>