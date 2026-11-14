"""Exercise real Anki APIs using temporary synthetic collections only."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import random
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument('--anki-path')
parser.add_argument('--screenshot')
args, remaining = parser.parse_known_args()
if args.anki_path:
    base = Path(args.anki_path)
    if hasattr(os, 'add_dll_directory'):
        _dll_handles = [os.add_dll_directory(str(base))]
        pywin = base / 'app_packages' / 'pywin32_system32'
        if pywin.exists():
            _dll_handles.append(os.add_dll_directory(str(pywin)))
    sys.path.insert(0, str(base / 'app_packages'))
    for extra in ('win32', 'win32/lib', 'Pythonwin', 'pywin32_system32'):
        sys.path.append(str(base / 'app_packages' / extra))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from anki.collection import Collection
from anki.cards import Card
from anki.decks import FilteredDeckConfig
from anki.scheduler.v3 import Scheduler, CardAnswer
import aqt
from aqt import gui_hooks
import anki.lang
anki.lang.set_lang('en')

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('true_random_test_package')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location(package.__name__ + '.scheduler', ROOT / 'scheduler.py')
engine = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = engine
spec.loader.exec_module(engine)
native_get = Scheduler.get_queued_cards
engine.install_scheduler()
from true_random_test_package import addon
addon.install()


class Integration(unittest.TestCase):
    def setUp(self):
        workspace = ROOT.parents[1] / 'work'
        self.tmp = tempfile.TemporaryDirectory(dir=workspace if workspace.exists() else None)
        self.col = Collection(str(Path(self.tmp.name) / 'collection.anki2'))
        aqt.mw = types.SimpleNamespace(col=self.col)
        self.ids = []
        for index in range(12):
            note = self.col.new_note(self.col.models.by_name('Basic'))
            note['Front'] = f'Synthetic card {index}'
            note['Back'] = f'Answer {index}'
            self.col.add_note(note, 1)
            self.ids.append(note.cards()[0].id)
        today = self.col.sched.today
        now = int(time.time())
        for index, cid in enumerate(self.ids):
            card = self.col.get_card(cid)
            if index % 4 == 1:
                card.type = card.queue = 2
                card.ivl = 7
                card.factor = 2500
                card.due = today
            elif index % 4 == 2:
                card.type = card.queue = 1
                card.due = now - 60
                card.left = 2002
            elif index % 4 == 3:
                card.type = 3
                card.queue = 1
                card.ivl = 7
                card.factor = 2500
                card.due = now - 60
                card.left = 1001
            self.col.update_card(card)
        self.deck_id = self.build()
        engine.set_enabled(self.col, self.deck_id, True)
        engine.session_for(self.col.sched).rng = random.Random(12345)

    def tearDown(self):
        aqt.mw = None
        self.col.close()
        self.tmp.cleanup()

    def build(self, second=False, limit=100, reschedule=True):
        deck = self.col.sched.get_or_create_filtered_deck(0)
        deck.name = 'Synthetic filtered'
        deck.config.reschedule = reschedule
        deck.config.search_terms[0].search = 'deck:Default'
        deck.config.search_terms[0].limit = limit
        deck.config.search_terms[0].order = FilteredDeckConfig.SearchTerm.Order.RANDOM
        if second:
            deck.config.search_terms[0].search += ' is:new'
            deck.config.search_terms[1].search = 'deck:Default -is:new'
            deck.config.search_terms[1].limit = limit
        else:
            del deck.config.search_terms[1:]
        return self.col.sched.add_or_update_filtered_deck(deck).id

    def answer(self, queued, rating=CardAnswer.GOOD):
        card = Card(self.col, backend_card=queued.card)
        card.start_timer()
        return self.col.sched.answer_card(
            input=self.col.sched.build_answer(card=card, states=queued.states, rating=rating)
        )

    def test_uniform_pool_and_idempotence(self):
        out = self.col.sched.get_queued_cards(fetch_limit=100)
        self.assertEqual({q.card.id for q in out.cards}, set(self.ids))
        self.assertEqual({q.card.ctype for q in out.cards}, {0, 1, 2, 3})
        self.assertEqual(out.new_count + out.learning_count + out.review_count, 12)
        self.assertEqual(self.col.sched.get_queued_cards().cards[0].card.id, out.cards[0].card.id)
        self.assertFalse(self.col.sched.get_queued_cards(fetch_limit=0).cards)
        self.assertEqual(self.col.sched.get_queued_cards().cards[0].card.id, out.cards[0].card.id)
        session = engine.session_for(self.col.sched)
        firsts = set()
        for seed in range(240):
            session.invalidate()
            session.rng.seed(seed)
            firsts.add(self.col.sched.get_queued_cards().cards[0].card.id)
        self.assertEqual(firsts, set(self.ids))

    def show(self, queued):
        # The final display can differ from the first scheduler suggestion.
        card = Card(self.col, backend_card=queued.card)
        gui_hooks.reviewer_did_show_question(card)
        return card

    def reopen(self, did=None):
        changes = self.col._backend.set_current_deck(did or self.deck_id)
        gui_hooks.operation_did_execute(changes, None)
        return self.col.sched.get_queued_cards()

    def test_resume_final_displayed_card_all_card_types(self):
        for kind in (0, 1, 2, 3):
            with self.subTest(kind=kind):
                queue = self.col.sched.get_queued_cards(fetch_limit=100)
                q = next(q for q in reversed(queue.cards) if q.card.ctype == kind)
                self.show(q)
                before = self.col.db.all('select * from cards order by id')
                for _ in range(5):
                    self.assertEqual(self.reopen().cards[0].card.id, q.card.id)
                self.assertEqual(before, self.col.db.all('select * from cards order by id'))
        self.assertEqual(self.col.db.scalar('select count(*) from revlog'), 0)

    def test_resume_survives_visiting_another_deck(self):
        q = self.col.sched.get_queued_cards().cards[0]
        self.show(q)
        self.reopen(1)
        self.assertEqual(self.reopen().cards[0].card.id, q.card.id)

    def test_resume_is_independent_for_two_filtered_decks(self):
        first = self.col.sched.get_queued_cards().cards[0]
        self.show(first)
        for index in range(3):
            note = self.col.new_note(self.col.models.by_name('Basic'))
            note['Front'] = f'Second deck {index}'
            self.col.add_note(note, 1)
        deck = self.col.sched.get_or_create_filtered_deck(0)
        deck.name = 'Second synthetic filtered'
        deck.config.search_terms[0].search = 'deck:Default'
        del deck.config.search_terms[1:]
        other = self.col.sched.add_or_update_filtered_deck(deck).id
        engine.set_enabled(self.col, other, True)
        second = self.reopen(other).cards[0]
        self.show(second)
        self.assertEqual(self.reopen().cards[0].card.id, first.card.id)
        self.assertEqual(self.reopen(other).cards[0].card.id, second.card.id)
        self.answer(second, CardAnswer.EASY)
        self.col.undo()
        self.assertEqual(self.reopen().cards[0].card.id, first.card.id)

    def test_resume_does_not_pin_answered_card(self):
        q = self.col.sched.get_queued_cards().cards[0]
        self.show(q)
        self.assertIn(self.deck_id, engine.session_for(self.col.sched).pending)
        self.answer(q, CardAnswer.EASY)
        self.assertNotIn(self.deck_id, engine.session_for(self.col.sched).pending)
        self.assertNotEqual(self.reopen().cards[0].card.id, q.card.id)
        self.col.undo()
        self.assertEqual(self.reopen().cards[0].card.id, q.card.id)

    def test_resume_discards_unavailable_and_rescheduled_cards(self):
        for change in ('bury', 'suspend', 'delete', 'future'):
            with self.subTest(change=change):
                q = self.col.sched.get_queued_cards().cards[0]
                self.show(q)
                cid = q.card.id
                if change == 'bury':
                    self.col.sched.bury_cards([cid])
                elif change == 'suspend':
                    self.col.sched.suspend_cards([cid])
                elif change == 'delete':
                    self.col.remove_notes([q.card.note_id])
                else:
                    card = self.col.get_card(cid)
                    card.type = card.queue = 1
                    card.due = int(time.time())+600
                    card.left = 2002
                    self.col.update_card(card)
                self.assertNotEqual(self.reopen().cards[0].card.id, cid)
                self.assertNotIn(self.deck_id, engine.session_for(self.col.sched).pending)

    def test_resume_survives_text_edit_but_not_schedule_edit(self):
        q = self.col.sched.get_queued_cards().cards[0]
        self.show(q)
        note = self.col.get_note(q.card.note_id)
        note['Front'] += ' edited'
        self.col.update_note(note)
        self.assertEqual(self.reopen().cards[0].card.id, q.card.id)
        card = self.col.get_card(q.card.id)
        card.reps += 1
        self.col.update_card(card)
        self.reopen()
        self.assertNotIn(self.deck_id, engine.session_for(self.col.sched).pending)

    def test_count_queries_do_not_create_resume_state(self):
        self.col.sched.get_queued_cards(fetch_limit=0)
        self.col.sched.get_queued_cards(fetch_limit=100)
        self.assertFalse(engine.session_for(self.col.sched).pending)

    def test_fetch_does_not_modify_cards_or_undo(self):
        before = self.col.db.all('select * from cards order by id')
        status = self.col._backend.get_undo_status().SerializeToString()
        for _ in range(3):
            self.col.sched.get_queued_cards(fetch_limit=100)
        self.assertEqual(before, self.col.db.all('select * from cards order by id'))
        self.assertEqual(status, self.col._backend.get_undo_status().SerializeToString())

    def test_out_of_order_answer_undo_redo(self):
        native = native_get(self.col.sched, fetch_limit=100)
        target = native.cards[-1]
        self.assertNotEqual(target.card.id, native.cards[0].card.id)
        before = self.col.get_card(target.card.id)._to_backend_card()
        old_history = self.col._backend.get_undo_status().undo
        before_log = self.col.db.scalar('select count(*) from revlog')
        self.answer(target)
        after = self.col.get_card(target.card.id)._to_backend_card()
        self.assertNotEqual(before, after)
        self.assertEqual(self.col.db.scalar('select count(*) from revlog'), before_log + 1)
        self.col.undo()
        self.assertEqual(before, self.col.get_card(target.card.id)._to_backend_card())
        self.assertEqual(self.col.db.scalar('select count(*) from revlog'), before_log)
        self.assertEqual(self.col._backend.get_undo_status().undo, old_history)
        self.assertEqual(self.col.sched.get_queued_cards().cards[0].card.id, target.card.id)
        self.col.redo()
        self.assertEqual(after, self.col.get_card(target.card.id)._to_backend_card())

    def test_each_card_state_answers_and_undoes(self):
        for kind in (0, 1, 2, 3):
            for rating in (CardAnswer.AGAIN, CardAnswer.HARD, CardAnswer.GOOD, CardAnswer.EASY):
                q = next(q for q in self.col.sched.get_queued_cards(fetch_limit=100).cards if q.card.ctype == kind)
                before = self.col.get_card(q.card.id)._to_backend_card()
                self.answer(q, rating=rating)
                self.col.undo()
                self.assertEqual(before, self.col.get_card(q.card.id)._to_backend_card())

    def test_multiple_answers_have_one_undo_step_each(self):
        snapshots = []
        for _ in range(4):
            q = self.col.sched.get_queued_cards().cards[0]
            snapshots.append((q.card.id, self.col.get_card(q.card.id)._to_backend_card()))
            self.answer(q, rating=CardAnswer.EASY)
        for cid, before in reversed(snapshots):
            self.col.undo()
            self.assertEqual(before, self.col.get_card(cid)._to_backend_card())
            self.assertEqual(self.col.sched.get_queued_cards().cards[0].card.id, cid)

    def test_failed_answer_does_not_change_scheduling(self):
        q = self.col.sched.get_queued_cards().cards[0]
        before = self.col.get_card(q.card.id)._to_backend_card()
        card = Card(self.col, backend_card=q.card)
        card.start_timer()
        answer = self.col.sched.build_answer(card=card, states=q.states, rating=CardAnswer.GOOD)
        answer.current_state.Clear()
        with self.assertRaises(Exception):
            self.col.sched.answer_card(answer)
        after = self.col.get_card(card.id)._to_backend_card()
        # Anki itself drops undo history on a backend transaction error. The
        # add-on must neither undo another operation nor change scheduling.
        after.mtime_secs = before.mtime_secs
        after.usn = before.usn
        self.assertEqual(before, after)

    def test_no_early_learning_when_only_future_cards_remain(self):
        for cid in self.ids:
            card = self.col.get_card(cid)
            card.type = 1
            card.queue = 1
            card.due = int(time.time()) + 60
            card.left = 2002
            self.col.update_card(card)
        out = self.col.sched.get_queued_cards()
        self.assertFalse(out.cards)
        self.assertEqual(out.learning_count, 0)

    def test_mode_persists_across_reopening_collection(self):
        path = self.col.path
        self.col.close()
        self.col = Collection(path)
        self.assertTrue(engine.is_enabled(self.col))
        self.assertEqual(len(self.col.sched.get_queued_cards(fetch_limit=100).cards), 12)

    def test_standard_answering_untouched_after_disable(self):
        engine.set_enabled(self.col, self.deck_id, False)
        q = self.col.sched.get_queued_cards().cards[0]
        before = self.col.get_card(q.card.id)._to_backend_card()
        self.answer(q)
        self.col.undo()
        self.assertEqual(before, self.col.get_card(q.card.id)._to_backend_card())

    def test_again_preserves_future_wait(self):
        q = next(q for q in self.col.sched.get_queued_cards(fetch_limit=100).cards if q.card.ctype == 0)
        self.answer(q, rating=CardAnswer.AGAIN)
        card = self.col.get_card(q.card.id)
        self.assertGreater(card.due, int(time.time()))
        print('Again result:', 'in filtered deck' if card.did == self.deck_id else 'returned home',
              'delay seconds:', card.due - int(time.time()))
        if card.did == self.deck_id:
            self.assertNotIn(card.id, {q.card.id for q in self.col.sched.get_queued_cards(fetch_limit=100).cards})

    def test_future_learning_waits_even_after_build(self):
        self.col.sched.empty_filtered_deck(self.deck_id)
        cid = self.ids[2]
        card = self.col.get_card(cid)
        future = int(time.time()) + 600
        card.due = future
        self.col.update_card(card)
        self.col.sched.rebuild_filtered_deck(self.deck_id)
        self.assertNotIn(cid, {q.card.id for q in self.col.sched.get_queued_cards(fetch_limit=100).cards})
        engine.session_for(self.col.sched).invalidate()
        # Make Anki rebuild its cutoff without altering the card's due time.
        current = self.col.get_card(self.ids[0])
        self.col.update_card(current)
        with patch.object(engine.time, 'time', return_value=future + 1):
            # Native includes original negative positions; add-on applies the
            # original timestamp and admits the learning card only after expiry.
            self.assertIn(cid, {q.card.id for q in self.col.sched.get_queued_cards(fetch_limit=100).cards})

    def test_suspended_and_buried_excluded(self):
        for cid, queue in zip(self.ids[:2], (-1, -2)):
            card = self.col.get_card(cid)
            card.queue = queue
            self.col.update_card(card)
        ids = {q.card.id for q in self.col.sched.get_queued_cards(fetch_limit=100).cards}
        self.assertFalse(set(self.ids[:2]) & ids)

    def test_disabled_uses_native_queue(self):
        engine.set_enabled(self.col, self.deck_id, False)
        self.assertEqual(native_get(self.col.sched, fetch_limit=100),
                         self.col.sched.get_queued_cards(fetch_limit=100))
        self.col.decks.select(1)
        self.assertFalse(engine.is_enabled(self.col))

    def test_two_filters_limits_and_mixing(self):
        self.col.sched.empty_filtered_deck(self.deck_id)
        second_id = self.build(second=True, limit=2)
        engine.set_enabled(self.col, second_id, True)
        out = self.col.sched.get_queued_cards(fetch_limit=100)
        self.assertEqual(len(out.cards), 4)
        self.assertEqual(sum(q.card.ctype == 0 for q in out.cards), 2)
        session = engine.session_for(self.col.sched)
        first_kinds = set()
        for seed in range(20):
            session.invalidate()
            session.rng.seed(seed)
            first_kinds.add(self.col.sched.get_queued_cards().cards[0].card.ctype == 0)
        self.assertEqual(first_kinds, {True, False})

    def test_preview_repeats_wait(self):
        self.col.sched.empty_filtered_deck(self.deck_id)
        preview_id = self.build(reschedule=False)
        engine.set_enabled(self.col, preview_id, True)
        q = self.col.sched.get_queued_cards().cards[0]
        self.answer(q, rating=CardAnswer.AGAIN)
        card = self.col.get_card(q.card.id)
        self.assertGreater(card.due, int(time.time()))
        self.assertNotIn(card.id, {q.card.id for q in self.col.sched.get_queued_cards(fetch_limit=100).cards})
        self.col.undo()
        self.assertEqual(self.col.sched.get_queued_cards().cards[0].card.id, card.id)

    def test_fsrs_states_and_context_preserved(self):
        self.col.set_config('fsrs', True)
        # Changing a card naturally invalidates the native queue.
        self.col.update_card(self.col.get_card(self.ids[0]))
        native = native_get(self.col.sched, fetch_limit=100)
        custom = self.col.sched.get_queued_cards(fetch_limit=100)
        expected = {q.card.id: q for q in native.cards}
        for q in custom.cards:
            self.assertEqual(q.context, expected[q.card.id].context)
            self.assertEqual(q.states, expected[q.card.id].states)
        target = next(q for q in custom.cards if q.card.ctype == 2)
        self.show(target)
        resumed = self.reopen().cards[0]
        self.assertEqual(resumed, target)
        self.answer(resumed)
        self.col.undo()

    def test_full_pool_over_1000_cards(self):
        # Native fetch uses a caller-supplied limit. Ensure no hidden 1000-card
        # prefix biases the add-on's sample.
        for index in range(1005):
            note = self.col.new_note(self.col.models.by_name('Basic'))
            note['Front'] = f'Large pool {index}'
            self.col.add_note(note, 1)
        deck = self.col.sched.get_or_create_filtered_deck(self.deck_id)
        deck.config.search_terms[0].limit = 2000
        self.col.sched.add_or_update_filtered_deck(deck)
        started = time.perf_counter()
        out = self.col.sched.get_queued_cards(fetch_limit=2000)
        self.assertEqual(len(out.cards), 1017)
        print('1017-card native pool fetch seconds:', round(time.perf_counter() - started, 3))

    def test_dialog_mapping_and_persistence(self):
        from aqt.qt import QApplication, QDialog
        from aqt.filtered_deck import FilteredDeckConfigDialog
        from aqt.forms.filtered_deck import Ui_Dialog
        from aqt import gui_hooks
        from true_random_test_package import addon
        addon.install()
        # Install is idempotent, including scheduler wrappers and hook registration.
        addon.install()
        app = QApplication.instance() or QApplication([])
        dialog = FilteredDeckConfigDialog.__new__(FilteredDeckConfigDialog)
        QDialog.__init__(dialog)
        dialog.col = self.col
        dialog.deck = self.col.sched.get_or_create_filtered_deck(self.deck_id)
        dialog.form = Ui_Dialog()
        dialog.form.setupUi(dialog)
        labels = self.col.sched.filtered_deck_order_labels()
        dialog._order_values = dialog._available_order_values(order_count=len(labels), fsrs_enabled=False)
        for combo in (dialog.form.order, dialog.form.order_2):
            combo.addItems([labels[i] for i in dialog._order_values])
        dialog._desired_search_1 = dialog._desired_search_2 = None
        try:
            dialog._load_deck()
            row = dialog._true_random_row
            self.assertEqual(dialog.form.order.currentText(), 'True random')
            self.assertEqual(dialog.form.order_2.currentText(), 'True random')
            self.assertTrue(dialog._update_deck())
            self.assertEqual(dialog.deck.config.search_terms[0].order, addon.RANDOM)
            dialog.form.order.setCurrentIndex(0)
            self.assertNotEqual(dialog.form.order_2.currentIndex(), row)
            dialog.form.order_2.setCurrentIndex(row)
            self.assertEqual(dialog.form.order.currentIndex(), row)
            # Loading twice must not insert duplicate options.
            size = dialog.form.order.count()
            addon.on_load(dialog, dialog.deck)
            self.assertEqual(dialog.form.order.count(), size)
            addon.on_will_save(dialog, dialog.deck)
            self.col.sched.add_or_update_filtered_deck(dialog.deck)
            addon.on_saved(dialog, dialog.deck, self.deck_id)
            self.assertTrue(engine.is_enabled(self.col))
            if args.screenshot:
                dialog.resize(720, 400)
                dialog.show()
                app.processEvents()
                dialog.grab().save(args.screenshot)
            dialog.form.order.setCurrentIndex(0)
            dialog._update_deck()
            addon.on_will_save(dialog, dialog.deck)
            self.col.sched.add_or_update_filtered_deck(dialog.deck)
            addon.on_saved(dialog, dialog.deck, self.deck_id)
            self.assertFalse(engine.is_enabled(self.col))
            self.col.undo()
            self.assertTrue(engine.is_enabled(self.col))
            self.col.redo()
            self.assertFalse(engine.is_enabled(self.col))
            # A subsequent ordinary build needs no custom undo group or config.
            addon.on_will_save(dialog, dialog.deck)
            self.assertIsNone(dialog._true_random_undo_id)
            before_config = self.col.get_config(engine.CONFIG_KEY)
            addon.on_saved(dialog, dialog.deck, self.deck_id)
            self.assertEqual(before_config, self.col.get_config(engine.CONFIG_KEY))
        finally:
            dialog.hide()
            dialog.deleteLater()


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)
