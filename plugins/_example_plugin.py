"""Copy this file to a name without the leading underscore, such as plugins/coin.py, to add a tool.

Files starting with an underscore aren't loaded. Tools here go through the same permission gate
as Fox's built-in tools: set risk to Risk.SAFE, Risk.NORMAL (the default) or Risk.CRITICAL.
"""

import random

from fox.core.permissions import Risk
from fox.core.tooling import fox_tool


@fox_tool(risk=Risk.SAFE)
def flip_coin() -> str:
    """Flip a coin. The model reads this docstring to learn what the tool does."""
    return random.choice(["heads", "tails"])
