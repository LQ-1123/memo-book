"""run_desktop.py — PyInstaller 入口脚本。

不能直接用 app/desktop.py 当入口：作为脚本运行时包内相对导入失效。
"""
from app.desktop import main

if __name__ == "__main__":
    main()
