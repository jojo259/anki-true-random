"""Build an Anki add-on archive containing only runtime files."""

import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
package = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['package']
target = root / f'{package}.ankiaddon'
with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
    for name in ('__init__.py', 'addon.py', 'scheduler.py', 'manifest.json', 'README.md'):
        archive.write(root / name, name)
print(target)
