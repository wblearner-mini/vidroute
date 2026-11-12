"""vidroute: give coding agents eyes and ears for local video.

The kernel decides *what* to hand an agent's LLM (native video, keyframes plus
a local transcript, or text only) from the effective capabilities of the
LLM and the agent host, then prepares those deliverables locally.
"""

__version__ = "0.1.0"

#: Version tag of the JSON envelope every command returns.
SCHEMA = "vidroute/1"

__all__ = ["SCHEMA", "__version__"]
