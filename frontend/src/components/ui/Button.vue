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
        validator: (value) => ['control', 'brand', 'on-contrast', 'tinted', 'important'].includes(value)
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
    },
    // Drawn over content that scrolls under it (a sticky Apply, a wizard's
    // footer): the tint and the disabled fade are laid over the panel's opaque
    // color, so nothing shows through the button.
    floating: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['click'])

const buttonClasses = computed(() => [
    'btn',
    props.size === 'small' ? 'heading-5' : 'heading-4',
    `btn--${props.variant}`,
    `btn--${props.size}`,
    {
        'btn--loading': props.loading && !props.disabled,
        'btn--floating': props.floating,
        'btn--with-icon': props.leftIcon || props.loading
    }
])

function handleClick(event) {
    if (!props.disabled) {
        emit('click', event)
    }
}
</script>

<style scoped>
.btn {
    background: var(--btn-fill);
    color: var(--btn-ink);
    text-align: center;
    border: none;
    cursor: pointer;
    transition: background var(--transition-fast), color var(--transition-fast), opacity var(--transition-fast), var(--transition-press);
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
    min-height: 48px;
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

/* === VARIANTS === */
/* Each variant names its fill and its ink; .btn paints them, and a floating
   button composes the same fill over an opaque base. */
.btn--control {
    --btn-fill: var(--color-control);
    --btn-ink: var(--color-text);
}

.btn--brand {
    --btn-fill: var(--color-brand);
    --btn-ink: var(--color-text-on-brand);
}

/* A glint on a contrast surface */
.btn--on-contrast {
    --btn-fill: var(--color-glint);
    --btn-ink: var(--color-text-on-contrast);
}

/* The secondary action: brand ink on a brand tint */
.btn--tinted {
    --btn-fill: var(--color-brand-subtle);
    --btn-ink: var(--color-brand);
}

/* A destructive action: red ink on a red tint */
.btn--important {
    --btn-fill: var(--color-error-subtle);
    --btn-ink: var(--color-error);
}

/* === STATES === */
/* Disabled (loading or not): the button as it is, faded, so it still says
   what it would do. */
.btn:disabled {
    opacity: var(--opacity-disabled);
}

.btn--loading {
    cursor: wait;
    pointer-events: none;
}

/* Floating: the fill over the panel, and the disabled fade as a veil of the
   panel over both rather than an opacity, which would let the content
   scrolling underneath show through. */
.btn--floating {
    background: linear-gradient(var(--btn-fill), var(--btn-fill)), var(--color-panel);
}

.btn--floating:disabled {
    --btn-veil: color-mix(in srgb, var(--color-panel) 60%, transparent);
    opacity: 1;
    background:
        linear-gradient(var(--btn-veil), var(--btn-veil)),
        linear-gradient(var(--btn-fill), var(--btn-fill)),
        var(--color-panel);
    color: color-mix(in srgb, var(--btn-ink) 40%, var(--color-panel));
}

/* === RESPONSIVE (Mobile) === */
@media (max-aspect-ratio: 4/3) {
    .btn--medium {
        min-height: 38px;
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