import sys
from pathlib import Path

# Rend le package "app" importable sans installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
