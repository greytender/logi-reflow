# Python 运行环境

本目录是 Python 官方的 Windows x64 embeddable package，供城配续航直接启动使用。

- 版本：CPython 3.14.6，与本次验证环境一致。
- 下载地址：https://www.python.org/ftp/python/3.14.6/python-3.14.6-embed-amd64.zip
- 官方发布页：https://www.python.org/downloads/release/python-3146/
- 原始压缩包 SHA-256：`df901e84a896ff1ee720ad03377e0c8d8c2244fda79808aeeaff6316df1cb75c`
- 许可证：见同目录 `LICENSE.txt`。

仅调整了 `python314._pth`，让解释器能够加载上级目录的启动脚本与 `runtime` 模块。没有安装第三方 Python 依赖。
