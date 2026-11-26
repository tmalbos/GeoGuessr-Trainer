"""clue_relations.py — The clue relations graph: same_as / similar_to / group, and how they propagate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from src.explore.country_clues.clues import _cc, _load

if TYPE_CHECKING:
    from src.explore.country_clues.edit_mode.endpoints import ClueBody

NOTE_TYPES = ("same_as", "similar_to")  # relations that carry a sentence


# ── relations ──────────────────────────────────────────────────────────────


def _relations_of(body: ClueBody, cc: str) -> tuple[list[dict], dict]:
    """Normalised relations + notes from the request. Raises 422 on anything inconsistent."""
    rels: list[dict] = []
    seen: set[tuple] = set()
    for r in body.relations:
        item = {"type": r.type, "cc": _cc(r.cc), "id": r.id}
        if r.type == "group" and item["cc"] != cc:
            raise HTTPException(422, "A group can only join clues of the same country")
        key = (item["type"], item["cc"], item["id"])
        if key not in seen:
            seen.add(key)
            rels.append(item)
    present = {r["type"] for r in rels}
    notes: dict[str, dict] = {}
    for t in NOTE_TYPES:
        if t not in present:
            continue
        n = body.relation_notes.get(t)
        text = n.text.strip() if n else ""
        if not text:
            raise HTTPException(422, f"Finish the sentence for the {t.replace('_', ' ')} relation")
        notes[t] = {"verb": n.verb, "text": text}
    return rels, notes


def _set_rels(clue: dict, rels: list[dict]) -> None:
    """Store `rels` on the clue and drop notes of relation types it no longer has."""
    if rels:
        clue["relations"] = rels
    else:
        clue.pop("relations", None)
    present = {r["type"] for r in rels}
    notes = {k: v for k, v in (clue.get("relation_notes") or {}).items() if k in present}
    if notes:
        clue["relation_notes"] = notes
    else:
        clue.pop("relation_notes", None)


def _get_clue(items_of, node: tuple[str, str]) -> dict | None:
    code, cid = node
    return next((c for c in items_of(code) if c.get("id") == cid), None)


def _neighbours(items_of, node: tuple[str, str], rtype: str) -> list[tuple[str, str]]:
    c = _get_clue(items_of, node)
    if c is None:
        return []
    return [(r["cc"], r["id"]) for r in c.get("relations") or [] if r["type"] == rtype]


def _same_class(items_of, start: tuple[str, str]) -> set[tuple[str, str]]:
    """Every clue connected to `start` through same_as links (start included)."""
    seen = {start}
    todo = [start]
    while todo:
        n = todo.pop()
        for m in _neighbours(items_of, n, "same_as"):
            if m not in seen and _get_clue(items_of, m) is not None:
                seen.add(m)
                todo.append(m)
    return seen


def _link(items_of, a: tuple[str, str], b: tuple[str, str], rtype: str, note: dict | None) -> None:
    """Make a and b point at each other with `rtype`. The sentence is copied only where one is missing."""
    if a == b:
        return
    ca, cb = _get_clue(items_of, a), _get_clue(items_of, b)
    if ca is None or cb is None:
        return
    for other, clue in ((b, ca), (a, cb)):
        rels = list(clue.get("relations") or [])
        rel = {"type": rtype, "cc": other[0], "id": other[1]}
        if rel not in rels:
            rels.append(rel)
        clue["relations"] = rels
        if note:
            notes = clue.get("relation_notes") or {}
            notes.setdefault(rtype, dict(note))
            clue["relation_notes"] = notes


def _close_equivalence(items_of, start: tuple[str, str], entry_notes: dict) -> None:
    """After a same_as / similar_to link was added to `start`, spread it to the clues that are equal to it.

    1. same_as: all clues in start's same_as class end up linked to each other.
    2. similar_to: every clue of that class is similar to every clue that any member is similar to,
       and to whatever those are the same as.
    """

    def note_for(rtype: str, nodes) -> dict | None:
        if entry_notes.get(rtype):
            return entry_notes[rtype]
        for node in nodes:
            c = _get_clue(items_of, node)
            n = ((c or {}).get("relation_notes") or {}).get(rtype)
            if n:
                return n
        return None

    cls = sorted(_same_class(items_of, start))
    same_note = note_for("same_as", cls)
    for i, a in enumerate(cls):
        for b in cls[i + 1 :]:
            _link(items_of, a, b, "same_as", same_note)

    targets: set[tuple[str, str]] = set()
    for member in cls:
        for t in _neighbours(items_of, member, "similar_to"):
            if _get_clue(items_of, t) is not None:
                targets |= _same_class(items_of, t)
    similar_note = note_for("similar_to", [*cls, *targets])
    for p in cls:
        same_as_p = set(_neighbours(items_of, p, "same_as"))
        for t in sorted(targets):
            if t == p or t in same_as_p:
                continue
            _link(items_of, p, t, "similar_to", similar_note)


def _sync_relations(
    books: dict[str, list[dict]], cc: str, entry: dict, old: list[dict], new: list[dict]
) -> None:
    """Make the clues on the other end mirror `entry`'s relations (old -> new).

    `books` maps country code -> its clue list; the current country's list MUST be the one that is about to
    be saved (so edits to clues of the same country aren't overwritten). Other countries are loaded lazily.
    The caller saves every list in `books` afterwards.
    """

    def items_of(code: str) -> list[dict]:
        if code not in books:
            books[code] = _load(code)
        return books[code]

    def find(code: str, clue_id: str) -> dict | None:
        return next((c for c in items_of(code) if c.get("id") == clue_id), None)

    def keys(rels: list[dict]) -> set[tuple]:
        return {(r["type"], r["cc"], r["id"]) for r in rels}

    old_k, new_k = keys(old), keys(new)
    added, removed = new_k - old_k, old_k - new_k

    for _, code, cid in added:  # validate everything before touching anything
        if code == cc and cid == entry["id"]:
            raise HTTPException(422, "A clue can't be related to itself")
        if find(code, cid) is None:
            raise HTTPException(422, "A related clue no longer exists. Reopen the editor.")

    for t, code, cid in removed:
        target = find(code, cid)
        if target is None:
            continue
        left = [
            r
            for r in target.get("relations") or []
            if not (r["type"] == t and r["cc"] == cc and r["id"] == entry["id"])
        ]
        _set_rels(target, left)

    for t, code, cid in added:
        target = find(code, cid)
        rels = list(target.get("relations") or [])
        back = {"type": t, "cc": cc, "id": entry["id"]}
        if back not in rels:
            rels.append(back)
        target["relations"] = rels
        note = (entry.get("relation_notes") or {}).get(t)
        if note:  # the other side gets the same sentence unless it already has its own
            notes = target.get("relation_notes") or {}
            notes.setdefault(t, dict(note))
            target["relation_notes"] = notes

    if any(t == "group" for t, _, _ in added):
        _close_group(items_of(cc), entry["id"], cc)

    if any(t in NOTE_TYPES for t, _, _ in added):
        _close_equivalence(items_of, (cc, entry["id"]), entry.get("relation_notes") or {})


def _close_group(items: list[dict], start_id: str, cc: str) -> None:
    """Groups are transitive: if 1 is grouped with 2 and with 3, then 2 and 3 are grouped too.

    Walks the group relations from `start_id` and makes every member of that group point at every other member.
    """
    by_id = {c.get("id"): c for c in items}

    def peers(cid: str) -> list[str]:
        return [
            r["id"]
            for r in by_id[cid].get("relations") or []
            if r["type"] == "group" and r["id"] in by_id
        ]

    members: list[str] = []
    todo = [start_id]
    while todo:
        cid = todo.pop()
        if cid in members or cid not in by_id:
            continue
        members.append(cid)
        todo.extend(peers(cid))

    for cid in members:
        rels = list(by_id[cid].get("relations") or [])
        have = {r["id"] for r in rels if r["type"] == "group"}
        rels.extend(
            {"type": "group", "cc": cc, "id": other}
            for other in members
            if other != cid and other not in have
        )
        by_id[cid]["relations"] = rels
