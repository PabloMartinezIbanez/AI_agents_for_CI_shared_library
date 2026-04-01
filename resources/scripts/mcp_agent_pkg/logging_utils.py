import sys


def log(msg):
    """Print to stderr (Jenkins captures stdout for artifacts)."""
    print(msg, file=sys.stderr, flush=True)

