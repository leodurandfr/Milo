// frontend/src/schemas/ws.js
/**
 * Zod schemas for WebSocket event payloads.
 *
 * Each entry maps a `(category, type)` pair (key format: `category.type`) to
 * the Zod schema describing the expected `event.data` shape. Handlers consume
 * the validated payload via `parsedOn(category, type, schema, handler)` — they
 * MUST NOT read `event.data.x` directly.
 *
 * The registry is intentionally partial: only fautive pairs (where the
 * frontend used to read `event.data.x` with dual-shape fallbacks) are
 * schematized. Other pairs continue to dispatch raw `event` via `on(...)`
 * until a future PR migrates them.
 *
 * To add a new schema: declare it below, expose it via `wsEventRegistry`,
 * and switch the consumer to `parsedOn('category', 'type', schema, handler)`.
 */
import { z } from 'zod';

export const wsEventRegistry = {
  // Filled progressively in subsequent commits (C3-C5).
};
