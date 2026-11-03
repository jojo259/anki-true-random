"""Add the mode to Anki's existing filtered-deck controls."""

from anki.decks import FilteredDeckConfig
from aqt import gui_hooks
from aqt.qt import QSignalBlocker, Qt

from .scheduler import (
    LABEL, TOOLTIP, install_scheduler, is_enabled, session_for, set_enabled,
)

RANDOM = FilteredDeckConfig.SearchTerm.Order.RANDOM
_installed = False


def on_load(dialog, deck) -> None:
    combos = (dialog.form.order, dialog.form.order_2)
    if not hasattr(dialog, "_true_random_row"):
        native_row = dialog._order_values.index(RANDOM)
        row = native_row + 1
        dialog._order_values.insert(row, RANDOM)
        dialog._true_random_row = row
        for combo in combos:
            combo.insertItem(row, LABEL)
            combo.setItemData(row, TOOLTIP, Qt.ItemDataRole.ToolTipRole)

        def changed(combo, index):
            other = combos[1] if combo is combos[0] else combos[0]
            blocker = QSignalBlocker(other)
            if index == row:
                other.setCurrentIndex(row)
            elif other.currentIndex() == row:
                other.setCurrentIndex(native_row)
            del blocker

        for combo in combos:
            combo.currentIndexChanged.connect(
                lambda index, combo=combo: changed(combo, index)
            )

    row = dialog._true_random_row
    if deck.id and is_enabled(dialog.col, deck.id):
        for combo in combos:
            blocker = QSignalBlocker(combo)
            combo.setCurrentIndex(row)
            del blocker


def on_will_save(dialog, deck) -> None:
    dialog._true_random_undo_id = None
    enabled = dialog.form.order.currentIndex() == dialog._true_random_row
    if not enabled and not (deck.id and is_enabled(dialog.col, deck.id)):
        return
    dialog._true_random_undo_id = dialog.col.add_custom_undo_entry("Build Filtered Deck")


def on_saved(dialog, deck, deck_id) -> None:
    undo_id = getattr(dialog, "_true_random_undo_id", None)
    if undo_id is None:
        return
    enabled = dialog.form.order.currentIndex() == dialog._true_random_row
    set_enabled(dialog.col, deck_id, enabled, undoable=True)
    # Undoing Build/Rebuild also restores the previous mode preference.
    dialog.col.merge_undo_entries(undo_id)
    dialog._true_random_undo_id = None


def on_operation(changes, handler) -> None:
    if changes.study_queues:
        from aqt import mw
        if mw and mw.col:
            session = session_for(mw.col.sched)
            # An Undo wrapper may have requested a particular restored card.
            session.invalidate(force_id=session.force_id)


def install() -> None:
    global _installed
    install_scheduler()
    if not _installed:
        gui_hooks.filtered_deck_dialog_did_load_deck.append(on_load)
        gui_hooks.filtered_deck_dialog_will_add_or_update_deck.append(on_will_save)
        gui_hooks.filtered_deck_dialog_did_add_or_update_deck.append(on_saved)
        gui_hooks.operation_did_execute.append(on_operation)
        _installed = True
