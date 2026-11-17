import { useSyncExternalStore } from "react";

// True while a drawing is still being built on the server (see DrawMap / ShadeDraw). The clue editor won't save until it is done.
let busy = false;
const subs = new Set();
const subscribe = (cb) => { subs.add(cb); return () => subs.delete(cb); };

export const setDrawBusy = (v) => {
  if (busy === v) return;
  busy = v;
  subs.forEach((cb) => cb());
};

export const useDrawBusy = () => useSyncExternalStore(subscribe, () => busy);
