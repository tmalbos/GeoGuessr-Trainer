// Keybindings shared by the Settings page and the components that listen for them.

/** True when the keyboard event matches a shortcut like "Ctrl+Shift+G". */
export const sameShortcut = (e, spec) => {
  const parts = String(spec).split("+").map((p) => p.trim().toLowerCase()).filter(Boolean);
  const key = parts.pop();
  const mods = new Set(parts);
  const wantCtrl = mods.has("ctrl") || mods.has("control");
  const wantMeta = mods.has("meta") || mods.has("cmd") || mods.has("win");
  return e.ctrlKey === wantCtrl && e.shiftKey === mods.has("shift") && e.altKey === mods.has("alt")
    && e.metaKey === wantMeta && !!key && e.key.toLowerCase() === key;
};

// One per admin level the draw editors can show (max 5 in the local data).
export const DEFAULT_DRAW_LEVEL_SHORTCUTS = ["Alt+1", "Alt+2", "Alt+3", "Alt+4", "Alt+5"];
