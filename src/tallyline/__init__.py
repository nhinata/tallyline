try:
    from ._version import __version__
except ImportError:  # running from a source checkout without an install
    __version__ = "0.0.0.dev0"
