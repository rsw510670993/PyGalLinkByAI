"""PHP CLI bridge for the agent job spool (mirrors pipeline_entry.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tool.agent_jobs import main

if __name__ == '__main__':
    sys.exit(main())
