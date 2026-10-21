#!/usr/bin/env python
"""Command-line wrapper: python make-laser-qr.py --config configs/<sign>.txt  (see qrsign/signmaker.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qrsign.signmaker import main  # noqa: E402

if __name__ == "__main__":
    main()
