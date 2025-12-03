# Codebase Consistency Analysis - Milo

This document identifies inconsistencies, non-standard patterns, and areas requiring further investigation in the Milo codebase.

---

## Executive Summary

| Category | Issues Found | Severity |
|----------|-------------|----------|
| Vue Component Patterns | 8 issues | Medium-High |
| Styling & CSS | 9 issues | Medium |
| State Management | 10 issues | High |
| TypeScript/Typing | 7 issues | High |
| **Total** | **34 issues** | |

---

## 1. Vue Component Inconsistencies

### 1.1 Mixed API Styles (HIGH PRIORITY)

**Problem**: 2 components use Options API while 43 use Composition API with `<script setup>`.

| File | API Style | Should Be |
|------|-----------|-----------|
| `frontend/src/components/ui/Button.vue` | Options API | `<script setup>` |
| `frontend/src/components/ui/Icon.vue` | Options API | `<script setup>` |

**Files to explore further:**
- `frontend/src/components/ui/Button.vue` - Lines 34-76 (entire script block)
- `frontend/src/components/ui/Icon.vue` - Lines 11-42 (entire script block)

### 1.2 Props Definition Inconsistency (MEDIUM)

**Problem**: Two patterns used interchangeably without clear convention.

**Pattern A - Variable assignment** (23+ components):
```javascript
const props = defineProps({ ... })
// Usage: props.modelValue
```

**Pattern B - Direct declaration** (20+ components):
```javascript
defineProps({ ... })
// Usage: directly in template without namespace
```

**Files to investigate:**
- `frontend/src/components/ui/InputText.vue` - Uses Pattern A
- `frontend/src/components/settings/SettingsCategory.vue` - Uses Pattern B
- `frontend/src/components/radio/StationCard.vue` - Uses Pattern B

### 1.3 Emits Definition Inconsistency (MEDIUM)

**Problem**: Mixed patterns for emit function assignment.

**Pattern A** - `const emit = defineEmits([...])` then `emit('event')`
**Pattern B** - `defineEmits([...])` then `$emit('event')` in template

**Files to investigate:**
- `frontend/src/components/ui/IconButtonFloating.vue` - Uses `$emit()` directly
- `frontend/src/components/radio/StationCard.vue` - Uses `$emit()` directly
- `frontend/src/components/settings/SettingsCategory.vue` - Uses `$emit()` directly

---

## 2. Styling Inconsistencies

### 2.1 Hardcoded Colors (HIGH PRIORITY)

**Problem**: CSS variables exist in design system but multiple components use hardcoded values.

| File | Line(s) | Hardcoded Value | Should Use |
|------|---------|-----------------|------------|
| `frontend/src/components/ui/RangeSlider.vue` | 144, 170 | `#767C76`, `#FFFFFF` | `var(--color-text-secondary)`, `var(--color-background-neutral)` |
| `frontend/src/components/ui/DoubleRangeSlider.vue` | Multiple | `#767C76`, `#FFFFFF` | Same as above |
| `frontend/src/components/ui/LoadingSpinner.vue` | JS block | `'#767C76'`, `'#FFFFFF'`, `'#F7F7F7'` | CSS variables |
| `frontend/src/components/ui/CircularIcon.vue` | ~50 | `rgba(0, 0, 0, 0.12)` | Design system variable |
| `frontend/src/components/ui/Toggle.vue` | Style block | `#999` | `var(--color-disabled)` (missing) |
| `frontend/src/components/settings/categories/radio/ManageStation.vue` | Multiple | `rgba(244, 67, 54, ...)` | `var(--color-error)` |
| `frontend/src/components/ui/VirtualKeyboard.vue` | Multiple | `rgba(0, 0, 0, 0.7)` | Design system variable |
| `frontend/src/components/librespot/PlaybackControls.vue` | Multiple | `rgba(255, 255, 255, 0.1)` | Design system variable |

### 2.2 Excessive `!important` Usage (MEDIUM)

**Problem**: 30+ instances of `!important` indicate specificity issues.

**Files to investigate:**
- `frontend/src/components/navigation/BottomNavigation.vue` - Lines 1087-1094
- `frontend/src/components/ui/AppIcon.vue` - Lines 166-180
- `frontend/src/components/ui/LoadingSpinner.vue` - Lines 114-115

### 2.3 Duplicate CSS Rules (LOW)

**Problem**: `.rounded-02` defined twice in design system.

**File to fix:**
- `frontend/src/assets/styles/design-system.css` - Lines 224-226

### 2.4 Inconsistent Class Naming (MEDIUM)

**Problem**: Similar components use different naming patterns for variants.

| Component | Pattern Example |
|-----------|-----------------|
| Button | `.btn--primary`, `.btn--active` |
| CircularIcon | `.circular-icon--light`, `.circular-icon--primary` |
| Modal | `.dropdown-trigger.is-open` (uses `is-` prefix) |

**Recommendation**: Standardize BEM naming convention across all components.

### 2.5 Empty Media Query (LOW)

**File to investigate:**
- `frontend/src/components/librespot/PlaybackControls.vue` - Has empty `@media` block

---

## 3. State Management Issues

### 3.1 Mixed HTTP Client Usage (HIGH PRIORITY)

**Problem**: Code mixes `axios` and native `fetch()` within the same store.

**File to investigate:**
- `frontend/src/stores/unifiedAudioStore.js`
  - Line 78: Uses `axios.post` for `setVolume()`
  - Line 96: Uses `fetch()` for `adjustVolume()`

**Impact**: Inconsistent error handling, request transformation, and authentication patterns.

### 3.2 Inconsistent Error Handling (HIGH PRIORITY)

**Problem**: Three different error handling patterns across stores.

| Pattern | Behavior | Files |
|---------|----------|-------|
| Return boolean | `return false` on error (silent) | `unifiedAudioStore.js` |
| Re-throw error | `throw error` for caller | `useSettingsAPI.js` |
| Set error state | `hasError.value = true` | `radioStore.js` |

**Files to investigate:**
- `frontend/src/stores/unifiedAudioStore.js` - Lines 36-39 (changeSource)
- `frontend/src/stores/snapcastStore.js` - Multiple catch blocks
- `frontend/src/stores/equalizerStore.js` - Multiple catch blocks

### 3.3 Inconsistent Response Validation (MEDIUM)

**Problem**: Backend responses validated differently.

| Store | Validation Check |
|-------|------------------|
| radioStore | `response.data.success` |
| unifiedAudioStore | `response.data.status === 'success'` |
| equalizerStore | `response.data.status === 'success'` |
| snapcastStore | Both patterns + existence checks |

**Recommendation**: Standardize API response contract.

### 3.4 Duplicate Data Arrays (HIGH PRIORITY)

**Problem**: Same station data maintained in 4 separate arrays.

**File to investigate:**
- `frontend/src/stores/radioStore.js`
  - `currentStations` - Full search results
  - `visibleStations` - Paginated view
  - `favoriteStations` - Favorites list
  - `currentStation` - Currently playing

**Impact**: Updates require manual sync across all arrays (Lines 416-419, 576-603).

### 3.5 Store-Component Coupling (HIGH PRIORITY)

**Problem**: Store directly references component methods.

**File to investigate:**
- `frontend/src/stores/unifiedAudioStore.js`
  - Lines 24, 231-233: `volumeBarRef` stores component reference
  - Line 236+: `volumeBarRef.value.showVolume()` calls component method

**Impact**: Breaks encapsulation, makes testing difficult.

### 3.6 Missing Loading States (MEDIUM)

**Problem**: Some stores lack loading indicators for async operations.

**Files to investigate:**
- `frontend/src/stores/unifiedAudioStore.js` - `changeSource()`, `sendCommand()` - no `isLoading` state
- `frontend/src/stores/snapcastStore.js` - Missing loading indicators

**Compare with**: `frontend/src/stores/radioStore.js` - Has proper `loading` state.

### 3.7 Conflicting Throttling Strategy (MEDIUM)

**Problem**: Dual-timeout approach may send same value up to 3 times.

**File to investigate:**
- `frontend/src/stores/equalizerStore.js` - Lines 125-148 (`handleBandThrottled`)
  - Immediate send
  - Throttled timeout send
  - Final timeout send (always scheduled)

### 3.8 Silent Fallbacks in Parallel Loading (MEDIUM)

**Problem**: All settings requests use silent fallbacks, masking failures.

**File to investigate:**
- `frontend/src/stores/settingsStore.js` - Lines 77-87
  - 9 parallel requests with `.catch(() => default)`
  - No indication which endpoint failed
  - No retry mechanism

### 3.9 Request Cancellation Missing (LOW)

**Problem**: Only `radioStore` implements `AbortController` pattern.

**Files lacking cancellation:**
- `frontend/src/stores/snapcastStore.js`
- `frontend/src/stores/equalizerStore.js`

### 3.10 Hardcoded Cache Duration (LOW)

**File to investigate:**
- `frontend/src/stores/radioStore.js` - Lines 28-33
  - `CACHE_DURATION_MS = 10 * 60 * 1000` (hardcoded)
  - `BACKGROUND_REFRESH_THRESHOLD_MS = 5 * 60 * 1000` (hardcoded)

---

## 4. TypeScript/Type Safety Issues

### 4.1 No TypeScript Usage (HIGH PRIORITY)

**Problem**: Entire codebase (62 JS/Vue files, ~9,200 lines) has no static type safety.

- No TypeScript configuration
- No `.ts` files
- No type definition files (`.d.ts`)
- No JSDoc annotations

### 4.2 Object Props Without Shape Definition (HIGH)

**Problem**: Complex objects passed as props without documented structure.

**Files to investigate:**
- `frontend/src/components/radio/StationCard.vue` - `station: { type: Object }` - What properties?
- `frontend/src/components/snapcast/SnapclientItem.vue` - `client: { type: Object }` - What properties?
- `frontend/src/components/snapcast/SnapcastControl.vue` - Config objects with nested properties

### 4.3 WebSocket Event Types Undefined (HIGH)

**File to investigate:**
- `frontend/src/services/websocket.js`
  - `handleMessage()` - No validation of message structure
  - Assumes `message.category` and `message.type` exist
  - Silent failure if message lacks required properties

### 4.4 API Response Data Unvalidated (HIGH)

**Files to investigate:**
- `frontend/src/stores/snapcastStore.js`
  - Deep property access: `response.data.config.file_config?.parsed_config?.stream`
  - No validation path exists
- `frontend/src/stores/radioStore.js`
  - `currentStations.value = response.data.stations` - No array validation

### 4.5 Function Parameters Untyped (MEDIUM)

**Examples:**
- `radioStore.js`: `addCustomStation(stationData)` - What is `stationData`?
- `websocket.js`: `on(category, type, callback)` - No callback signature
- `useVirtualKeyboard.js`: `open(options = {})` - Options shape undocumented

### 4.6 Return Values Inconsistent (MEDIUM)

**File to investigate:**
- `frontend/src/stores/radioStore.js`
  - `loadStations()` returns `boolean`
  - `addCustomStation()` returns `{ success, station?, error? }`
  - `fetchClients()` returns `array`

### 4.7 Destructuring Without Validation (MEDIUM)

**File to investigate:**
- `frontend/src/stores/snapcastStore.js`
  - `const { client_id, client_name, ... } = event.data`
  - No check that properties exist
  - Creates `undefined` variables silently

---

## 5. Priority Investigation Areas

### Tier 1 - Critical (Fix Immediately)

| Issue | File | Lines |
|-------|------|-------|
| Mixed axios/fetch | `stores/unifiedAudioStore.js` | 78, 96 |
| Duplicate data arrays | `stores/radioStore.js` | Full file |
| Store-component coupling | `stores/unifiedAudioStore.js` | 24, 231-236 |
| Options API components | `components/ui/Button.vue`, `Icon.vue` | Full files |
| Hardcoded colors | `components/ui/RangeSlider.vue` | 144, 170 |

### Tier 2 - Important (Fix Soon)

| Issue | File | Lines |
|-------|------|-------|
| Silent error handling | `stores/unifiedAudioStore.js` | 36-39 |
| Missing loading states | `stores/unifiedAudioStore.js`, `snapcastStore.js` | Multiple |
| Object props untyped | `components/radio/StationCard.vue` | Props block |
| WebSocket message validation | `services/websocket.js` | handleMessage |
| !important overuse | `components/navigation/BottomNavigation.vue` | 1087-1094 |

### Tier 3 - Improvements (Technical Debt)

| Issue | File | Lines |
|-------|------|-------|
| Props/emits pattern inconsistency | Multiple components | - |
| Hardcoded cache duration | `stores/radioStore.js` | 28-33 |
| Empty media query | `components/librespot/PlaybackControls.vue` | Style block |
| Duplicate CSS rules | `assets/styles/design-system.css` | 224-226 |
| Missing AbortController | `stores/snapcastStore.js`, `equalizerStore.js` | - |

---

## 6. Recommendations

### Immediate Actions

1. **Standardize HTTP client** - Use axios consistently throughout all stores
2. **Create error handling utility** - Centralize error handling pattern
3. **Migrate Button.vue & Icon.vue** - Convert to Composition API `<script setup>`
4. **Replace hardcoded colors** - Use CSS variables from design system
5. **Add missing CSS variable** - Create `--color-disabled` for toggle states

### Short-term Actions

1. **Add JSDoc annotations** - Document function parameters and return types
2. **Create type definitions** - Define shapes for Station, Client, Settings objects
3. **Remove volumeBarRef coupling** - Use store-only state for volume bar visibility
4. **Normalize station data** - Single source of truth instead of 4 arrays
5. **Add loading states** - All async operations should set loading flag

### Long-term Actions

1. **TypeScript migration** - Incremental conversion starting with stores
2. **API response validation** - Add runtime validation with Zod or similar
3. **CSS architecture review** - Standardize BEM naming, remove !important usage
4. **Error boundary component** - Handle errors consistently across app
5. **Design system documentation** - Document all tokens and usage guidelines

---

## Appendix: Files Requiring Detailed Review

```
frontend/src/stores/
├── unifiedAudioStore.js    # HTTP mixing, coupling, missing loading
├── radioStore.js           # Duplicate arrays, inconsistent returns
├── snapcastStore.js        # Missing validation, no cancellation
├── equalizerStore.js       # Throttling issues
└── settingsStore.js        # Silent fallbacks

frontend/src/components/ui/
├── Button.vue              # Options API
├── Icon.vue                # Options API
├── RangeSlider.vue         # Hardcoded colors
├── DoubleRangeSlider.vue   # Hardcoded colors
├── LoadingSpinner.vue      # Hardcoded colors, !important
├── Toggle.vue              # Missing CSS variable
└── CircularIcon.vue        # Hardcoded rgba

frontend/src/services/
└── websocket.js            # Missing message validation

frontend/src/assets/styles/
└── design-system.css       # Duplicate rules
```

---

*Analysis generated on: 2025-12-03*
