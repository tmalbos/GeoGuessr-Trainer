import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import "./rich-editor.css";

// Same syntax RichText (richText.jsx) renders: **bold**  *italic*  __underline__  [text](https://url).
// The stored value stays that string. Bold / italic / underline are shown formatted while editing;
// links (and bare URLs) are the one exception and stay as typed.
const TOKEN = /\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<]*[^\s<.,;:!?)])/g;

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/** Stored string -> HTML for the editor. */
export function toHtml(text) {
  let out = "";
  let last = 0;
  for (const m of text.matchAll(TOKEN)) {
    out += esc(text.slice(last, m.index));
    if (m[1] != null) out += `<b>${toHtml(m[1])}</b>`;
    else if (m[2] != null) out += `<u>${toHtml(m[2])}</u>`;
    else if (m[3] != null) out += `<i>${toHtml(m[3])}</i>`;
    else out += esc(m[0]); // link or URL: left exactly as written
    last = m.index + m[0].length;
  }
  out += esc(text.slice(last));
  return out.replace(/\n/g, "<br>");
}

/** Editor DOM -> stored string. */
function serialize(node) {
  let s = "";
  node.childNodes.forEach((n) => {
    if (n.nodeType === 3) { s += n.nodeValue.replace(/\u00a0/g, " "); return; }
    if (n.nodeType !== 1) return;
    const tag = n.tagName;
    if (tag === "BR") { s += "\n"; return; }
    const inner = serialize(n);
    if (tag === "B" || tag === "STRONG") s += inner.trim() ? `**${inner}**` : inner;
    else if (tag === "I" || tag === "EM") s += inner.trim() ? `*${inner}*` : inner;
    else if (tag === "U") s += inner.trim() ? `__${inner}__` : inner;
    else if (tag === "DIV" || tag === "P") s += (s && !s.endsWith("\n") ? "\n" : "") + inner;
    else s += inner;
  });
  return s;
}

/** Controlled by `value` (the stored string). The parent drives the toolbar through the ref:
 *  ref.current.format("bold" | "italic" | "underline"), ref.current.link(). */
const RichEditor = forwardRef(function RichEditor({ value, onChange, placeholder, label }, ref) {
  const el = useRef(null);
  const last = useRef(null); // the last string this editor produced / was given

  useEffect(() => {
    if (value !== last.current) {
      el.current.innerHTML = toHtml(value);
      last.current = value;
    }
  }, [value]);

  const emit = () => {
    let v = serialize(el.current);
    if (!v.trim()) { el.current.innerHTML = ""; v = ""; } // lets the placeholder show again
    last.current = v;
    onChange(v);
  };

  useImperativeHandle(ref, () => ({
    format(cmd) {
      el.current.focus();
      document.execCommand(cmd);
      emit();
    },
    link() {
      el.current.focus();
      const sel = window.getSelection()?.toString() ?? "";
      document.execCommand("insertText", false, `[${sel}](https://)`);
      emit();
    },
  }), []); // eslint-disable-line react-hooks/exhaustive-deps

  const onKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      document.execCommand("insertLineBreak");
      emit();
    }
  };

  // Pasted text goes in as text; any **markers** in it become real formatting.
  const onPaste = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const t = e.clipboardData.getData("text/plain");
    if (t) { document.execCommand("insertHTML", false, toHtml(t)); emit(); }
  };

  return <div ref={el} className="rich-ed" contentEditable suppressContentEditableWarning role="textbox" aria-multiline="true"
    aria-label={label} data-placeholder={placeholder} spellCheck onInput={emit} onKeyDown={onKeyDown} onPaste={onPaste} />;
});

export default RichEditor;
