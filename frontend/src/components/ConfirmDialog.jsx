import { useEffect, useRef } from "react";

export default function ConfirmDialog({ title, body, confirmLabel = "Confirm", cancelLabel = "Cancel", danger, onConfirm, onCancel }) {
  const ok = useRef(null);
  useEffect(() => {
    ok.current?.focus();
    // Capture phase: Escape closes only this dialog, not whatever sits underneath it.
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); onCancel(); } };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onCancel]);
  return <div className="confirm-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
    <div className="confirm" role="alertdialog" aria-modal="true" aria-label={title}>
      <h3>{title}</h3>
      {body && <p className="muted">{body}</p>}
      <div className="confirm-actions">
        <button type="button" className="btn ghost" onClick={onCancel}>{cancelLabel}</button>
        <button type="button" ref={ok} className={`btn ${danger ? "danger" : ""}`} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </div>
  </div>;
}
