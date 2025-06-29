<!-- frontend/src/components/ui/Button.vue -->
<template>
    <button :class="buttonClasses" :disabled="disabled" @click="handleClick">
        <slot></slot>
    </button>
</template>

<script>
export default {
    name: 'Button',
    props: {
        variant: {
            type: String,
            default: 'primary',
            validator: (value) => ['primary', 'secondary', 'toggle'].includes(value)
        },
        disabled: {
            type: Boolean,
            default: false
        },
        active: {
            type: Boolean,
            default: false
        }
    },
    computed: {
        buttonClasses() {
            const baseClasses = 'btn text-body'
            const variantClass = `btn--${this.variant}`
            const stateClass = this.getStateClass()

            return `${baseClasses} ${variantClass} ${stateClass}`.trim()
        }
    },
    methods: {
        getStateClass() {
            if (this.variant === 'toggle') {
                return this.active ? 'btn--active' : 'btn--inactive'
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
    padding: var(--space-02) var(--space-04);
    text-align: center;
    border: none;
    cursor: pointer;
    transition: var(--transition-fast);
    border-radius: var(--radius-04);
}

.btn:disabled {
    cursor: not-allowed;
}

/* Primary variant */
.btn--primary.btn--default {
    background-color: var(--color-brand);
    color: var(--color-text-contrast);
}

.btn--primary.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* Secondary variant */
.btn--secondary.btn--default {
    background-color: var(--color-background-strong);
    color: var(--color-text-secondary);
}

.btn--secondary.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}

/* Toggle variant */
.btn--toggle.btn--active {
    background-color: var(--color-background-neutral);
    color: var(--color-brand);
    -webkit-box-shadow: inset 0px 0px 0px 2px var(--color-brand);
    -moz-box-shadow: inset 0px 0px 0px 2px var(--color-brand);
    box-shadow: inset 0px 0px 0px 2px var(--color-brand);}

.btn--toggle.btn--inactive {
    background-color: var(--color-background-strong);
    color: var(--color-text-secondary);

}
</style>