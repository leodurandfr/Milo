<!-- frontend/src/components/ui/Button.vue -->
<template>
    <button :class="buttonClasses" :disabled="disabled" @click="handleClick">
        <Icon v-if="leftIcon" :name="leftIcon" :size="32" />
        <slot></slot>
    </button>
</template>

<script>
import Icon from './Icon.vue'

export default {
    name: 'Button',
    components: {
        Icon
    },
    props: {
        variant: {
            type: String,
            default: 'primary',
            validator: (value) => ['primary', 'secondary', 'toggle', 'background-light'].includes(value)
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
        }
    },
    computed: {
        buttonClasses() {
            const baseClasses = 'btn text-body'
            const variantClass = `btn--${this.variant}`
            const stateClass = this.getStateClass()
            const iconClass = this.leftIcon ? 'btn--with-icon' : ''

            return `${baseClasses} ${variantClass} ${stateClass} ${iconClass}`.trim()
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
    padding: var(--space-03) var(--space-04);
    text-align: center;
    border: none;
    cursor: pointer;
    transition: var(--transition-fast);
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

/* Background Light variant */
.btn--background-light.btn--default {
    background-color: var(--color-background-neutral-12);
    color: var(--color-text-contrast);
}

.btn--background-light.btn--disabled {
    background-color: var(--color-background);
    color: var(--color-text-light);
}
</style>