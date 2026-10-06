"""bowerbird - turn a slide deck into a narrated video, in your own voice, locally."""

__version__ = "0.1.0"

from .config import Config, load as load_config  # noqa: F401
from .script import Slide, ScriptError, load as load_script, budget  # noqa: F401

__all__ = ["Config", "load_config", "Slide", "ScriptError", "load_script",
           "budget", "__version__"]
