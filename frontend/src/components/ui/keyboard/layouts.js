// frontend/src/components/ui/keyboard/layouts.js

/**
 * The static per-language key rows and accent variants the virtual keyboard renders.
 * A language with no entry here falls back to english, like locales/ does.
 *
 * Only the abc rows and the currency key differ between languages — the digit,
 * punctuation and symbol rows are shared, so adding a language means three rows
 * and two characters, not nine rows.
 */

// Row lengths are fixed by the CSS grid: row1/row2 fill 10 columns, row3 fills 8
// because [caps] and [enter] take the first and last column.
const DIGIT_ROW = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'];
const SYMBOL_ROW = ['[', ']', '{', '}', '#', '%', '^', '*', '+', '='];
const PUNCTUATION_ROW = [',', '?', '!', "'", '…', 'ō', '・', '—'];

const numbersRow2 = (currency) => ['-', '/', ':', ';', '(', ')', currency, '&', '@', '"'];
const symbolsRow2 = (currency) => ['_', '\\', '|', '~', '<', '>', currency, '£', '¥', '⸱'];

const languages = {
  french: {
    numbersCurrency: '€',
    symbolsCurrency: '$',
    abc: {
      row1: ['a', 'z', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['q', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'm'],
      row3: ['w', 'x', 'c', 'v', 'b', 'n', '?', ','],
    }
  },
  english: {
    numbersCurrency: '$',
    symbolsCurrency: '€',
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', "'"],
      row3: ['z', 'x', 'c', 'v', 'b', 'n', 'm', ','],
    }
  },
  spanish: {
    numbersCurrency: '€',
    symbolsCurrency: '$',
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'ñ'],
      row3: ['z', 'x', 'c', 'v', 'b', 'n', 'm', ','],
    }
  },
  german: {
    numbersCurrency: '€',
    symbolsCurrency: '$',
    abc: {
      row1: ['q', 'w', 'e', 'r', 't', 'z', 'u', 'i', 'o', 'p'],
      row2: ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', 'ü'],
      row3: ['y', 'x', 'c', 'v', 'b', 'n', 'm', ','],
    }
  }
};

// Native accents only — long-pressing a key with no entry inserts nothing extra.
const accents = {
  french: {
    'a': ['à', 'â', 'æ'],
    'e': ['è', 'é', 'ê', 'ë'],
    'i': ['î', 'ï'],
    'o': ['ô', 'œ'],
    'u': ['ù', 'û', 'ü'],
    'y': ['ÿ'],
    'c': ['ç'],
  },
  english: {},
  spanish: {
    'a': ['á'],
    'e': ['é'],
    'i': ['í'],
    'o': ['ó'],
    'u': ['ú', 'ü'],
    'n': ['ñ'],
    '?': ['¿'],
    '!': ['¡'],
  },
  german: {
    'a': ['ä'],
    'o': ['ö'],
    'u': ['ü'],
    's': ['ß'],
  },
};

export function keyLayout(language) {
  const lang = languages[language] || languages.english;
  return {
    abc: lang.abc,
    numbers: { row1: DIGIT_ROW, row2: numbersRow2(lang.numbersCurrency), row3: PUNCTUATION_ROW },
    symbols: { row1: SYMBOL_ROW, row2: symbolsRow2(lang.symbolsCurrency), row3: PUNCTUATION_ROW },
  };
}

export function accentVariants(language) {
  return accents[language] || accents.english;
}
