import { useState } from "react";
import GAMES from "./games.js";
import "./study.css";

export default function Study() {
  const [id, setId] = useState(null);
  const game = GAMES.find((g) => g.id === id);
  if (game) return <game.Component onExit={() => setId(null)} />;
  return <><h2>Study</h2>
    <div className="grid">{GAMES.map((g) =>
      <button key={g.id} className="card study-card" onClick={() => setId(g.id)}>
        <b>{g.title}</b><div className="muted">{g.description}</div>
      </button>)}</div></>;
}
