"""PHP CLI bridge for the EGS dashboard."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tool.pipeline import main
if __name__ == '__main__':
    main()
