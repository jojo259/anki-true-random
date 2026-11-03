# True Random

Adds **True random** to the **Selected by** dropdown in Anki's filtered-deck
options. Ready learning cards are mixed with the other cards instead of being
shown first. Learning delays still apply.

## Use

1. Install `true-random.ankiaddon` through **Tools → Add-ons → Install from file**.
2. Restart Anki.
3. Open a filtered deck's options and choose **True random**, immediately below
   **Random** in **Selected by**.
4. Click **Build** or **Rebuild**, then study normally.

Selecting an ordinary order switches the deck back to normal Anki behavior.

## Behavior

- Each ready card has the same chance of being selected, across new, review,
  learning and relearning cards. Repeated fetches keep the current card stable.
- Searches and card limits determine which cards enter the filtered deck.
  Suspended and buried cards remain excluded.
- With two filters, each filter retains its own search and limit. True Random
  mixes their resulting cards together. Selecting the mode in either dropdown
  enables it for the whole filtered deck.
- Learning and preview-repeat cards wait until their timestamp is due, including
  learning cards already waiting when the deck is built. This mode does not
  use Anki's learn-ahead window to show them early.
- Answer buttons, FSRS scheduling states, review history, sibling handling and
  returning cards to their home decks remain handled by Anki.
- Undo restores the previous answer and shows that card again. Redo works too.
- Only filtered decks explicitly set to this mode are affected.

## Compatibility and storage

Targets Anki Desktop **26.09.2**, using its v3 scheduler and filtered-deck dialog.
The mode uses desktop add-on code; AnkiMobile, AnkiDroid, AnkiWeb and desktops
without this add-on fall back to Anki's ordinary **Random** behavior.

The native order remains `RANDOM`. Opted-in deck IDs are stored in the collection
configuration under `true_random_filtered_decks`, so the preference can sync
between desktop installations with this add-on. No custom scheduler enum is
written to the collection.

To support choosing a card outside the Rust queue's usual order, the add-on saves
the card with unchanged fields immediately before answering. This refreshes the
native queue. That save and the actual answer are combined into one native
**Answer Card** undo step. No card is rewritten merely to fetch or display it.

The full native candidate queue is fetched before selection. Very large filtered
decks require more work per answer than ordinary Anki selection.

## Development

Run the integration suite with a Python interpreter and matching `anki` and
`aqt` packages, or point it at an installed Anki's `app_packages` directory:

```sh
python tests/test_integration.py --anki-path /path/to/Anki
```

When testing against an installed Anki, use the same Python minor version as that
installation. Tests create only temporary synthetic collections.

Build the installable archive:

```sh
python tools/build.py
```
