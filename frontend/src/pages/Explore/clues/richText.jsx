// **bold**  *italic*  __underline__  [text](https://url)  and bare http(s) URLs. Line breaks are kept by CSS.
const TOKEN = /\*\*(.+?)\*\*|__(.+?)__|\*(.+?)\*|\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<]*[^\s<.,;:!?)])/g;

const link = (key, href, children) =>
  <a key={key} href={href} target="_blank" rel="noopener noreferrer">{children}</a>;

function render(text) {
  const out = [];
  let last = 0, i = 0;
  for (const m of text.matchAll(TOKEN)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const key = i++;
    if (m[1] != null) out.push(<b key={key}>{render(m[1])}</b>);
    else if (m[2] != null) out.push(<u key={key}>{render(m[2])}</u>);
    else if (m[3] != null) out.push(<i key={key}>{render(m[3])}</i>);
    else if (m[4] != null) out.push(link(key, m[5], render(m[4])));
    else out.push(link(key, m[6], m[6]));
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function RichText({ text }) {
  return <>{render(text)}</>;
}
