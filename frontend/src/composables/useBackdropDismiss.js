// A click fires on the common ancestor of press and release, so a drag that starts
// inside (a slider thumb) and ends on the backdrop reads as a backdrop click: dismiss
// only when the press started on the backdrop too. Bind onPointerdown in the capture
// phase — a thumb stops its own pointerdown from propagating.
export function useBackdropDismiss(dismiss) {
  let pressedBackdrop = false;

  return {
    onPointerdown(event) {
      pressedBackdrop = event.target === event.currentTarget;
    },
    onClick(event) {
      if (pressedBackdrop && event.target === event.currentTarget) dismiss();
      pressedBackdrop = false;
    }
  };
}
