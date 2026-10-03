import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

const Ctx = createContext({ push: () => 0, dismiss: () => {} });
let seq = 0;

/** push(message, { kind: "info"|"ok"|"err", ms, action: { label, onClick }, onExpire }).
 *  onExpire runs when the toast times out or is closed with ×, but NOT when the action is used (e.g. Undo). */
export function ToastProvider({ children }) {
  const [items, setItems] = useState([]);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setItems((l) => l.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((message, { kind = "info", ms = 4000, action, onExpire } = {}) => {
    const id = ++seq;
    setItems((l) => [...l, { id, message, kind, action, onExpire }]);
    timers.current.set(id, setTimeout(() => { dismiss(id); onExpire?.(); }, ms));
    return id;
  }, [dismiss]);

  const value = useMemo(() => ({ push, dismiss }), [push, dismiss]);
  return <Ctx.Provider value={value}>
    {children}
    <div className="toasts" role="status" aria-live="polite">
      {items.map((t) => <div key={t.id} className={`toast ${t.kind}`}>
        <span>{t.message}</span>
        {t.action && <button type="button" className="toast-act" onClick={() => { dismiss(t.id); t.action.onClick(); }}>{t.action.label}</button>}
        <button type="button" className="toast-x" aria-label="Dismiss" onClick={() => { dismiss(t.id); t.onExpire?.(); }}>×</button>
      </div>)}
    </div>
  </Ctx.Provider>;
}

export const useToast = () => useContext(Ctx);
