"""Uniform selection for opted-in filtered decks, using Anki's scheduling states."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from anki.scheduler.v3 import QueuedCards

CONFIG_KEY = "true_random_filtered_decks"
LABEL = "True random"
TOOLTIP = (
    "Randomly mix cards from both filters without prioritizing learning cards. "
    "Learning cards wait until their delay expires."
)


def is_enabled(col, deck_id: int | None = None) -> bool:
    deck = col.decks.current() if deck_id is None else col.decks.get(deck_id)
    return bool(
        deck
        and deck.get("dyn")
        and str(deck["id"]) in col.get_config(CONFIG_KEY, [])
    )


def set_enabled(col, deck_id: int, enabled: bool, *, undoable: bool = False) -> None:
    ids = set(col.get_config(CONFIG_KEY, []))
    if enabled:
        ids.add(str(deck_id))
    else:
        ids.discard(str(deck_id))
    # This config API preserves the existing undo history.
    col.set_config(CONFIG_KEY, sorted(ids), undoable=undoable)
    session_for(col.sched).invalidate()


def ready(queued, now: int) -> bool:
    card = queued.card
    if card.queue < 0:
        return False
    if card.queue in (1, 4):
        due = card.due
        # Building a filtered deck gives cards negative positions. A learning
        # card's original timestamp still determines when it is ready.
        if due < 0 and card.original_due > 1_000_000_000:
            due = card.original_due
        return due <= now
    return True


@dataclass
class Session:
    rng: random.Random = field(default_factory=random.Random)
    order: list[int] = field(default_factory=list)
    signature: tuple | None = None
    force_id: int | None = None
    answers: dict[int, int] = field(default_factory=dict)

    def invalidate(self, force_id: int | None = None) -> None:
        self.order.clear()
        self.signature = None
        self.force_id = force_id

    def select(self, scheduler, original, fetch_limit: int, intraday_only: bool):
        col = scheduler.col
        deck_id = col.decks.current()["id"]
        # Fetch the whole eligible queue, not a capped prefix, so a card's
        # chance is independent of its native queue and position.
        count = col.db.scalar("select count(*) from cards where did = ?", deck_id)
        native = original(scheduler, fetch_limit=max(1, count), intraday_learning_only=False)
        now = int(time.time())
        candidates = [q for q in native.cards if ready(q, now)]
        by_id = {q.card.id: q for q in candidates}
        signature = (
            deck_id,
            col._backend.get_undo_status().last_step,
            tuple(sorted(by_id)),
        )
        if signature != self.signature:
            # Preserve a displayed card if only time/counts changed. An answer,
            # Undo, or a collection operation explicitly invalidates selection.
            first = self.order[0] if self.order and self.order[0] in by_id else None
            self.order = list(by_id)
            self.rng.shuffle(self.order)
            if first is not None:
                self.order.remove(first)
                self.order.insert(0, first)
            self.signature = signature
        if self.force_id in by_id:
            self.order.remove(self.force_id)
            self.order.insert(0, self.force_id)
            self.force_id = None

        out = QueuedCards()
        for q in candidates:
            if q.queue == QueuedCards.NEW:
                out.new_count += 1
            elif q.queue == QueuedCards.LEARNING:
                out.learning_count += 1
            else:
                out.review_count += 1
        selected = [by_id[cid] for cid in self.order]
        if intraday_only:
            selected = [q for q in selected if q.card.queue in (1, 4)]
        else:
            selected = selected[:max(0, fetch_limit)]
        out.cards.extend(selected)
        return out

    def answer(self, scheduler, original, answer):
        col = scheduler.col
        card = col.get_card(answer.card_id)
        # Anki's Rust queue only accepts the head of the main queue. Refresh
        # it by saving the unchanged card immediately before the actual answer.
        # Merge both operations into one native undo step. The answer itself
        # still uses Anki's states, FSRS, review log and sibling handling.
        undo_id = col.add_custom_undo_entry(col.tr.actions_answer_card())
        prepared = False
        try:
            col.update_card(card)
            prepared = True
            col.merge_undo_entries(undo_id)
            original(scheduler, answer)
        except Exception:
            # Anki clears undo history on a failed backend transaction. Only
            # undo preparation if it is still the current, known undo entry
            # (for example, another add-on rejected the answer before Anki).
            status = col._backend.get_undo_status()
            if prepared and status.undo and status.last_step == undo_id:
                col.undo()
            self.invalidate()
            raise
        changes = col.merge_undo_entries(undo_id)
        self.answers[undo_id] = card.id
        # Native undo history is bounded; avoid retaining unlimited session data.
        if len(self.answers) > 100:
            self.answers.pop(next(iter(self.answers)))
        self.invalidate()
        return changes


def session_for(scheduler) -> Session:
    if not hasattr(scheduler, "_true_random_session"):
        scheduler._true_random_session = Session()
    return scheduler._true_random_session


def install_scheduler() -> None:
    from anki.collection import Collection
    from anki.scheduler.v3 import Scheduler

    if getattr(Scheduler.get_queued_cards, "_true_random", False):
        return
    original_get = Scheduler.get_queued_cards
    original_answer = Scheduler.answer_card
    original_undo = Collection.undo
    original_redo = Collection.redo

    def get_queued_cards(self, *, fetch_limit=1, intraday_learning_only=False):
        if not is_enabled(self.col):
            return original_get(self, fetch_limit=fetch_limit,
                                intraday_learning_only=intraday_learning_only)
        return session_for(self).select(self, original_get, fetch_limit, intraday_learning_only)

    def answer_card(self, input):
        if not is_enabled(self.col):
            return original_answer(self, input)
        card = self.col.get_card(input.card_id)
        if card.did != self.col.decks.current()["id"]:
            return original_answer(self, input)
        return session_for(self).answer(self, original_answer, input)

    def undo(self):
        out = original_undo(self)
        session = session_for(self.sched)
        session.invalidate(force_id=session.answers.get(out.counter))
        return out

    def redo(self):
        out = original_redo(self)
        session_for(self.sched).invalidate()
        return out

    get_queued_cards._true_random = True
    Scheduler.get_queued_cards = get_queued_cards
    Scheduler.answer_card = answer_card
    Collection.undo = undo
    Collection.redo = redo
