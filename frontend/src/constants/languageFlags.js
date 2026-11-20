// frontend/src/constants/languageFlags.js
/**
 * The flag drawn beside each UI language, keyed by its Milō code — one table
 * for the setup wizard's language step, its summary and the Language settings,
 * which each carried a copy. English is drawn as one flag split along its
 * diagonal, the United States' and the United Kingdom's: the locale is en-US
 * (CLAUDE.md § Language), but the language is not one country's.
 */
import franceIcon from '@/assets/flags-icons/france.svg';
import englishIcon from '@/assets/flags-icons/united-kingdom-united-states.svg';
import spainIcon from '@/assets/flags-icons/spain.svg';
import indiaIcon from '@/assets/flags-icons/india.svg';
import chinaIcon from '@/assets/flags-icons/china.svg';
import portugalIcon from '@/assets/flags-icons/portugal.svg';
import italyIcon from '@/assets/flags-icons/italy.svg';
import germanyIcon from '@/assets/flags-icons/germany.svg';

const FLAGS = {
  french: franceIcon,
  english: englishIcon,
  spanish: spainIcon,
  hindi: indiaIcon,
  chinese: chinaIcon,
  portuguese: portugalIcon,
  italian: italyIcon,
  german: germanyIcon,
};

/** The flag's URL for a language code, or '' when none ships for it. */
export function languageFlag(code) {
  return FLAGS[code] || '';
}
