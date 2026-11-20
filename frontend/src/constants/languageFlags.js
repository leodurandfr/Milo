// frontend/src/constants/languageFlags.js
/**
 * The flag drawn beside each UI language, keyed by its Milō code — one table
 * for the setup wizard's language step, its summary and the Language settings,
 * which each carried a copy. The English flag is the United Kingdom's, as all
 * three copies drew it, although `english` is en-US (CLAUDE.md § Language):
 * whether it becomes the United States' is the owner's call, still open.
 */
import franceIcon from '@/assets/flags-icons/france.svg';
import unitedKingdomIcon from '@/assets/flags-icons/united-kingdom.svg';
import spainIcon from '@/assets/flags-icons/spain.svg';
import indiaIcon from '@/assets/flags-icons/india.svg';
import chinaIcon from '@/assets/flags-icons/china.svg';
import portugalIcon from '@/assets/flags-icons/portugal.svg';
import italyIcon from '@/assets/flags-icons/italy.svg';
import germanyIcon from '@/assets/flags-icons/germany.svg';

const FLAGS = {
  french: franceIcon,
  english: unitedKingdomIcon,
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
