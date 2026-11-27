// frontend/src/utils/clipboard.js
/**
 * Text onto the clipboard, from any page Milō serves.
 *
 * navigator.clipboard exists only in a secure context. The kiosk browses
 * http://localhost, which is one; a phone browsing http://milo.local is not,
 * and there the selection-based path is the only one that works. Answers
 * whether the text was copied, so the caller can offer another way when not.
 */
export async function writeToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Fall through: a permission refusal still leaves the path below.
    }
  }
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  area.style.position = 'fixed';
  area.style.opacity = '0';
  document.body.appendChild(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  }
  document.body.removeChild(area);
  return ok;
}
