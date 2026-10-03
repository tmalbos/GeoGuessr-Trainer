import { navigate } from "../../lib/router.js";
import GAMES from "./games.js";
import "./study.css";

// #/study = menu, #/study/scripts = a minigame
export default function Study({ seg }) {
  const game = GAMES.find((g) => g.id === seg[0]);
  if (game) return <game.Component onExit={() => navigate("/study")} />;
  return <><h2>Study</h2>
    <div className="grid">{GAMES.map((g) =>
      <button key={g.id} className="card study-card" onClick={() => navigate(`/study/${g.id}`)}>
        <b>{g.title}</b><div className="muted">{g.description}</div>
      </button>)}</div></>;
}
