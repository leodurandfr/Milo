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
 *
 * The same file holds the vocabulary each locale settled on (`GLOSSARY`, at the
 * end): the word a translator uses for a speaker, a zone, a station… and the
 * synonyms that pass for it. Register and vocabulary drift the same way — one
 * batch of strings at a time, from a neutral English — and are read the same
 * way, as a list of forms that must not appear.
 *
 * Goes red too when a locale names one concept two ways.
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
    // `te` is tú's clitic (`Te damos la bienvenida`); usted's is `le`.
    'tú', 'tu', 'tus', 'ti', 'te', 'tuyo', 'tuya', 'tuyos', 'tuyas',
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

/**
 * One word per concept, per locale.
 *
 * Each entry is `[term, rejected]`: `term` is the word the locale uses for the
 * concept (a stem, matched case-insensitively anywhere — `enceinte` covers
 * `enceintes`), `rejected` the synonyms it must not use beside it. A user who
 * reads "enceinte" in one dialog and "haut-parleur" in the next is entitled to
 * think they are two different devices; a German who reads "Lautstärke" for a
 * loudness preset next to the Volume app has been told it is the same thing.
 *
 * The first four concepts are the audio vocabulary every screen shares. The
 * rest are the ones the meaning-and-consistency pass found named two ways, so
 * they list a rejected form only where it was actually found — like the
 * register lists above, never a guess at the language. Where a concept has no
 * rejected form in a locale, the term alone is the glossary: checked present,
 * so an entry that stops describing the tree goes red instead of passing on a
 * word no string uses.
 *
 * A rejected form is a bare substring, so it is banned in every sense, not only
 * the concept's: `délai` refuses a sleep timeout too, `estación` a season. A
 * translator who needs the word in another meaning narrows the entry here (a
 * longer phrase, or a key filter) rather than rewording around it.
 *
 * Choices worth knowing before translating:
 * - french: a Milō speaker is an `enceinte` (feminine: `Réinitialisez-la`);
 *   multiroom delay is `retard` — `délai` is a deadline.
 * - portuguese is pt-PT: a speaker is a `coluna`; `Definições` is the Settings
 *   screen, `palavra-passe` the password, `recetor`/`detetar` post-1990
 *   spelling, `ligar` connects a device, a network or an account, so
 *   `desligar` disconnects one; the Shut down action is `encerrar`, since a
 *   second `Desligar` would make Disconnect and Shut down one button (the
 *   hostname-conflict sentence says `Encerre`, the verb of its button).
 * - spanish: a radio station is an `emisora`, never an `estación`.
 * - chinese: a speaker is an 音箱, a zone a 区域 (地区 is a time-zone area),
 *   an artist 艺术家, a playlist 播放列表, the library 音乐库.
 * - hindi: WiFi, Ethernet and Bluetooth stay in Latin script; restart is
 *   पुनः आरंभ, transitive or not (`पुनः आरंभ हो रहा है`), try again पुनः प्रयास करें;
 *   फिर से शुरू करें stays for resume, which is another action.
 */
const GLOSSARY = {
  source: {
    french: ['source', []], spanish: ['fuente', []], italian: ['sorgent', []],
    portuguese: ['fonte', []], german: ['quelle', []], chinese: ['音源', ['音频源']],
    hindi: ['स्रोत', []],
  },
  speaker: {
    french: ['enceinte', ['haut-parleur']], spanish: ['altavo', []],
    italian: ['altoparlant', ['diffusor']], portuguese: ['coluna', ['altifalante']],
    german: ['lautsprecher', []], chinese: ['音箱', ['扬声器']], hindi: ['स्पीकर', []],
  },
  zone: {
    french: ['zone', []], spanish: ['zona', []], italian: ['zona', []],
    portuguese: ['zona', []], german: ['zone', []], chinese: ['区域', ['分区']],
    hindi: ['ज़ोन', []],
  },
  equalizer: {
    french: ['égaliseur', []], spanish: ['ecualizador', []], italian: ['equalizzatore', []],
    portuguese: ['equalizador', []], german: ['equalizer', []], chinese: ['均衡器', []],
    hindi: ['इक्वलाइज़र', []],
  },
  delay: { french: ['retard', ['délai']] },
  station: { spanish: ['emisora', ['estación', 'estaciones']] },
  artist: { chinese: ['艺术家', ['艺人']] },
  playlist: { chinese: ['播放列表', ['歌单']] },
  library: { chinese: ['音乐库', ['资料库']] },
  audioCard: { chinese: ['声卡', ['音频卡']] },
  settings: { portuguese: ['definições', ['configurações']] },
  password: { portuguese: ['palavra-passe', ['senha']] },
  receiver: { portuguese: ['recetor', ['receptor']] },
  detect: { portuguese: ['detet', ['detect']] },
  controls: { portuguese: ['controlo', ['controle']] },
  connect: { portuguese: ['ligad', ['conect', 'conex']] },
  index: { german: ['indiziert', ['indexiert', 'indexierung']] },
  restart: {
    hindi: ['पुनः आरंभ', ['रीस्टार्ट', 'रीबूट', 'पुनरारंभ', 'फिर से शुरू हो', 'फिर से शुरू हुआ', 'दोबारा शुरू']],
  },
  retry: { hindi: ['पुनः प्रयास', ['दोबारा कोशिश', 'फिर से प्रयास']] },
  ethernet: { hindi: ['ethernet', ['ईथरनेट']] },
  bluetooth: { hindi: ['bluetooth', ['ब्लूटूथ']] },
};

describe('locale glossary', () => {
  const stringsOf = name => Object.entries(locales[name])
    .filter(([, value]) => typeof value === 'string');

  for (const [concept, byLocale] of Object.entries(GLOSSARY)) {
    for (const [name, [term, rejected]] of Object.entries(byLocale)) {
      it(`${name} names "${concept}" one way: ${term}`, () => {
        const strings = stringsOf(name);
        expect(
          strings.some(([, value]) => value.toLowerCase().includes(term)),
          `${name} never uses "${term}" — the glossary no longer describes it`,
        ).toBe(true);
        const offenders = strings
          .filter(([, value]) => rejected.some(word => value.toLowerCase().includes(word)))
          .map(([key, value]) => `${key}: ${value}`);
        expect(offenders).toEqual([]);
      });
    }
  }
});

/**
 * Actions that must never read alike, as groups of keys: within an entry, no
 * string of one group may contain a string of another (equality included).
 *
 * The glossary above checks a word per concept; it cannot see two concepts
 * collapsing onto one word, which is the worse failure — two buttons that look
 * like one action. Portuguese had exactly that: `Desligar` was both Shut down
 * and Disconnect, so `A desligar…` read as the unit shutting down while a phone
 * was being unpaired. A glossary entry for the shutdown verb would only check
 * that the word appears somewhere, and stay green while the power button went
 * back to `Desligar`.
 *
 * Containment rather than equality, so a longer label that reuses the other
 * action's verb (`Desligar este dispositivo` beside `Desligar`) is caught too.
 * The keys are named here; every string compared is read from the locales.
 */
const DISTINCT_ACTIONS = {
  'shut down vs disconnect': [
    ['settings.shutdown', 'settings.shutdownInProgress', 'system.hostnameConflict.shutdown'],
    ['status.disconnect', 'status.disconnecting', 'qobuzSettings.disconnect'],
  ],
};

describe('locale distinct actions', () => {
  const normalize = value => value.toLowerCase().replace(/[….]+$/u, '').trim();

  for (const [pair, groups] of Object.entries(DISTINCT_ACTIONS)) {
    for (const name of Object.keys(locales)) {
      it(`${name} keeps ${pair} apart`, () => {
        const read = groups.map(keys => keys.map(key => {
          const value = locales[name][key];
          expect(typeof value, `${name} has no string at ${key}`).toBe('string');
          expect(normalize(value).length, `${name}: ${key} is empty`).toBeGreaterThan(0);
          return [key, normalize(value)];
        }));
        const clashes = [];
        read.forEach((group, i) => read.forEach((other, j) => {
          if (i === j) return;
          for (const [key, value] of group) {
            for (const [otherKey, otherValue] of other) {
              if (value.includes(otherValue)) clashes.push(`${key} contains ${otherKey}: ${otherValue}`);
            }
          }
        }));
        expect(clashes).toEqual([]);
      });
    }
  }
});
