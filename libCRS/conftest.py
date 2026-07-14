# SPDX-License-Identifier: MIT
"""Make the ``libCRS`` package importable when tests run from the repo root.

libCRS is not installed into the dev venv (it is installed into CRS container
images at build time), so put this directory — which contains the ``libCRS``
package — on ``sys.path`` for pytest collection.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
