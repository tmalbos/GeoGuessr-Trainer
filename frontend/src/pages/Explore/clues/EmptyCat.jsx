// Shown when a country has no visible clues yet.
export default function EmptyCat() {
  return <div className="cat-empty">
    <svg viewBox="0 0 200 170" role="img" aria-label="A sad cat">
      <path d="M50 74 L56 22 L94 52 Z" fill="var(--sand)" />
      <path d="M150 74 L144 22 L106 52 Z" fill="var(--sand)" />
      <path d="M60 58 L62 36 L80 50 Z" fill="var(--coral)" opacity=".45" />
      <path d="M140 58 L138 36 L120 50 Z" fill="var(--coral)" opacity=".45" />
      <ellipse cx="100" cy="102" rx="64" ry="54" fill="var(--sand)" />
      <path d="M62 90 L90 80 M138 90 L110 80" stroke="var(--on-sand)" strokeWidth="4" strokeLinecap="round" fill="none" />
      <ellipse cx="78" cy="100" rx="9" ry="12" fill="var(--on-sand)" />
      <ellipse cx="122" cy="100" rx="9" ry="12" fill="var(--on-sand)" />
      <circle cx="75" cy="95" r="3.2" fill="var(--sand)" />
      <circle cx="119" cy="95" r="3.2" fill="var(--sand)" />
      <path className="cat-tear" d="M70 114 q-6 9 0 13 q6 -4 0 -13z" fill="var(--sea-hi)" />
      <path d="M94 112 h12 l-6 7z" fill="var(--coral)" />
      <path d="M100 119 v3 M88 134 Q100 122 112 134" stroke="var(--on-sand)" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <path d="M50 112 L22 106 M50 122 L22 128 M150 112 L178 106 M150 122 L178 128" stroke="var(--on-sand)" strokeWidth="2.5" strokeLinecap="round" opacity=".6" />
    </svg>
    <h3>Nothing to see yet</h3>
  </div>;
}
