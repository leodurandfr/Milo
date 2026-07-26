// frontend/tests/pure/musicLibraryFormat.test.js
/**
 * Music Library durations arrive from Navidrome in **seconds**, while the
 * now-playing wire convention everywhere else is milliseconds. These helpers sit
 * exactly on that boundary, so the unit they assume is the thing worth pinning.
 */
import { describe, it, expect } from 'vitest';
import { formatDuration, totalMinutes, formatAudioQuality } from '@/components/music-library/format';

describe('formatDuration', () => {
  it('renders m:ss below an hour', () => {
    expect(formatDuration(0)).toBe('0:00');
    expect(formatDuration(5)).toBe('0:05');
    expect(formatDuration(65)).toBe('1:05');
    expect(formatDuration(599)).toBe('9:59');
  });

  it('renders h:mm:ss from an hour up', () => {
    expect(formatDuration(3600)).toBe('1:00:00');
    expect(formatDuration(3661)).toBe('1:01:01');
    expect(formatDuration(45296)).toBe('12:34:56');
  });

  it('truncates fractional seconds', () => {
    expect(formatDuration(59.9)).toBe('0:59');
  });

  it('treats missing or negative input as zero', () => {
    expect(formatDuration(undefined)).toBe('0:00');
    expect(formatDuration(null)).toBe('0:00');
    expect(formatDuration(-30)).toBe('0:00');
  });

  it('reads its input as seconds, not milliseconds', () => {
    // A ms value slipping in here would render as a 50-hour album.
    expect(formatDuration(180)).toBe('3:00');
  });
});

describe('totalMinutes', () => {
  it('rounds to the nearest minute', () => {
    expect(totalMinutes(3661)).toBe(61);
    expect(totalMinutes(29)).toBe(0);
    expect(totalMinutes(30)).toBe(1);
  });

  it('treats missing input as zero', () => {
    expect(totalMinutes(undefined)).toBe(0);
    expect(totalMinutes(null)).toBe(0);
  });
});

describe('formatAudioQuality', () => {
  it('drops the decimal for whole kHz rates', () => {
    expect(formatAudioQuality(24, 96000)).toBe('24B-96kHz');
    expect(formatAudioQuality(16, 48000)).toBe('16B-48kHz');
  });

  it('keeps one decimal for 44.1 kHz', () => {
    expect(formatAudioQuality(16, 44100)).toBe('16B-44.1kHz');
    expect(formatAudioQuality(24, 88200)).toBe('24B-88.2kHz');
  });

  it('renders nothing when either value is missing', () => {
    // The meta line must collapse rather than show "undefinedB-NaNkHz".
    expect(formatAudioQuality(undefined, 44100)).toBe('');
    expect(formatAudioQuality(16, undefined)).toBe('');
    expect(formatAudioQuality(0, 0)).toBe('');
  });
});
