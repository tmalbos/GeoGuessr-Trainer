// Sections of a country's clues page (shown as tabs at the top of the right-hand panel).
//
// To add one: build a component that takes { id, name, editing } (id = ISO code, e.g. "AR"),
// import it here and add one entry:
//   { id: "roads", label: "Roads", Component: Roads, available: (id) => id in ROADS }
// `available` is optional. Without it the section shows for every country; with it, only
// where it returns true. A country with no available sections shows "Coming soon".
import ClueList from "./clues/ClueList.jsx";

export default [{ id: "clues", label: "Clues", Component: ClueList }];
