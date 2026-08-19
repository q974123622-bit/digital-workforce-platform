"""CLI 入口：`python -m app.kb_index --rebuild`（实现见 app/services/kb_index.py）。"""

from .services.kb_index import main

if __name__ == "__main__":
    main()
