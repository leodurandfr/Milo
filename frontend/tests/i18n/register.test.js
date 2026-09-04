// frontend/tests/i18n/register.test.js
/**
 * Guardrail over the address register of the 8 locales.
 *
 * Milō addresses the user formally in every language that distinguishes
 * (CLAUDE.md § Language). English is canonical and neutral — `Select your
 * country` marks neither register — so a translator working from it gets no
 * signal, and four separate commits each landed a batch of strings in whatever
 * register their author reached for. That drift is invisible to
 * `locales.test.js`, which compares key sets and placeholders only.
 *
 * A denylist, deliberately: the formal register has no positive marker in
 * Spanish or Italian, where `Compruebe los datos` carries no pronoun at all.
 * The listed forms are the ones actually found in the tree when the register
 * was unified, not a guess at the language — extend a list when a new form
 * slips through rather than reaching for a general rule.
 *
 * Goes red when a locale starts addressing the user informally.
 */
import { describe, it, expect } from 'vitest';
import { loadLocales } from '../helpers/i18nScan';

const locales = loadLocales();

/**
 * Informal second-person markers, matched anywhere.
 *
 * Pronouns and possessives carry the whole rule on their own; the verb forms
 * beside them are restricted to shapes no third person can wear — clitics
 * (`déjalo`, `spegnilo`) and 2nd-singular presents (`puedes`, `puoi`).
 *
 * French adds its `-s` imperatives: `éteins` is not `éteint`, so unlike the
 * `-er` verbs — whose imperative is spelled like the indicative a feature
 * description legitimately uses — they cannot be anything but an address. That
 * omission is what let `Éteins l'un des deux` sit unflagged.
 */
const ADDRESS = {
  french: [
    'tu', 'ton', 'ta', 'tes', 'toi',
    'éteins', 'choisis', 'prends', 'viens', 'reviens', 'fais', 'mets', 'sais',
  ],
  spanish: [
    'tú', 'tu', 'tus', 'ti', 'tuyo', 'tuya', 'tuyos', 'tuyas',
    'puedes', 'quieres', 'tienes', 'hayas', 'conectes',
    'inténtalo', 'conéctate', 'déjalo', 'actívala', 'actívalo', 'desactívalo',
    'suscríbete', 'asegúrate',
  ],
  italian: [
    'tuo', 'tua', 'tuoi', 'tue',
    'puoi', 'vuoi', 'devi', 'preferisci', 'ricolleghi',
    'spegnilo', 'spegnine', 'scegline',
    'attivalo', 'aggiungilo', 'rinominala', 'cambiala',
  ],
  portuguese: ['teu', 'tua', 'teus', 'tuas', 'podes', 'queres', 'tens', 'fizeste'],
  german: [
    'du', 'dich', 'dir', 'dein', 'deine', 'deinem', 'deinen', 'deiner', 'deines',
    'wähle', 'verbinde', 'melde', 'überprüfe', 'tippe', 'drücke', 'gib',
    'beginne', 'spiele', 'verzögere', 'benenne', 'schalte', 'verwende',
    'lade', 'abonniere', 'kehre',
  ],
  chinese: ['你'],
  hindi: ['तुम', 'तेरा', 'तेरी'],
};

/**
 * Informal imperatives that are also the conventional form of an Italian
 * action label, so they only condemn a *sentence*.
 *
 * Italian is the one locale whose buttons are imperatives rather than
 * infinitives — `Salva`, `Riprova`, `Scegli un'immagine` are what every Italian
 * interface ships, and they address nobody. The same verb inside a sentence is
 * an order given to the reader, and there the register is real: `Verifica che
 * la scheda…` had to become `Verifichi che la scheda…`.
 */
const ITALIAN_IMPERATIVES = [
  'verifica', 'controlla', 'inserisci', 'premi', 'attendi', 'tocca', 'lascia',
  'collega', 'ritarda', 'riproduci', 'inizia', 'disattiva', 'usa',
  // Reflexives, which a label wears just as readily: `Iscriviti` is what every
  // Italian interface puts on a Subscribe button.
  'connettiti', 'iscriviti',
];

/**
 * A sentence, as opposed to a label. Word count alone misfires — the label
 * `Usa la rete del server ({ssid})` runs to six words — so the length bar sits
 * above the longest label in the tree and final punctuation catches the rest.
 */
function isSentence(value) {
  return /[.!?…]$/.test(value.trim()) || value.trim().split(/\s+/).length >= 7;
}

/**
 * Word-boundary match that survives accents and non-Latin scripts.
 *
 * `\b` is ASCII in JavaScript, so `\büberprüfe\b` never fires: `ü` is not a word
 * character, and a space beside it forms no boundary. Two entries were inert
 * for exactly that reason before the lookarounds below replaced it.
 *
 * Chinese writes no spaces, so 你 sits flush against the next character and any
 * boundary at all would be wrong — `你的国家` has to match on the pronoun alone.
 */
const UNSPACED_SCRIPTS = new Set(['chinese']);

function markerPattern(words, { unspaced = false } = {}) {
  const body = `(?:${words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`;
  // A form followed by a genitive is a noun, not an order: `Verifica dello
  // stato…` is "status check", where `Verifica che…` is "check that…".
  const notGenitive = '(?!\\s+(?:di|del|dello|della|dei|degli|delle|dell)(?![\\p{L}]))';
  return new RegExp(
    unspaced ? body : `(?<![\\p{L}\\p{N}])${body}${notGenitive}(?![\\p{L}\\p{N}])`,
    'iu',
  );
}

describe('locale address register', () => {
  // English is canonical and draws no formal/informal distinction, so it has
  // no rule — but a rule that silently scanned nothing would pass too.
  it('covers every locale, each with a non-trivial number of strings', () => {
    expect(Object.keys(locales).sort()).toEqual(
      [...Object.keys(ADDRESS), 'english'].sort()
    );
    for (const [name, strings] of Object.entries(locales)) {
      expect(Object.keys(strings).length, `${name} looks empty`).toBeGreaterThan(500);
    }
  });

  for (const [name, words] of Object.entries(ADDRESS)) {
    const unspaced = UNSPACED_SCRIPTS.has(name);
    const pattern = markerPattern(words, { unspaced });
    const imperatives = name === 'italian'
      ? markerPattern(ITALIAN_IMPERATIVES)
      : null;

    describe(name, () => {
      // A listed form that cannot match is a rule believed to be enforced and
      // enforcing nothing — which is what `überprüfe` was.
      it('can match every form it lists', () => {
        for (const word of [...words, ...(name === 'italian' ? ITALIAN_IMPERATIVES : [])]) {
          const re = imperatives && ITALIAN_IMPERATIVES.includes(word) ? imperatives : pattern;
          const neighboured = unspaced ? `${word}的国家` : `Milō ${word} test`;
          expect(re.test(neighboured), `${word} never matches`).toBe(true);
        }
      });

      it('addresses the user formally', () => {
        const offenders = Object.entries(locales[name])
          .filter(([, value]) => typeof value === 'string' && (
            pattern.test(value) || (imperatives && isSentence(value) && imperatives.test(value))
          ))
          .map(([key, value]) => `${key}: ${value}`);
        expect(offenders).toEqual([]);
      });
    });
  }
});
