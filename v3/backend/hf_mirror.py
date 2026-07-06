"""统一设置 Hugging Face 下载镜像（P305）。

必须在任何 huggingface_hub / faster_whisper / mlx_whisper 导入前 import 本模块——
huggingface_hub 在首次导入时把 HF_ENDPOINT 读进 constants.ENDPOINT，之后再改无效。
两个进程入口（main.py、worker.py）都在最顶部 import 本模块。
"""
import os

DEFAULT_HF_MIRROR = "https://hf-mirror.com"

# HF_ENDPOINT 用 setdefault：如果用户已有自己的镜像/代理（如企业内网镜像），保留不覆盖。
# NO_PROXY 不用 setdefault，而是追加：我们只是往列表加一项，不会影响用户已有的规则。
os.environ.setdefault("HF_ENDPOINT", DEFAULT_HF_MIRROR)

# 把镜像域名加进 NO_PROXY，让 requests 对 hf-mirror.com 跳过代理、直连。
# 背景：装了代理软件（Clash/V2Ray）的用户，终端里会有 HTTPS_PROXY=127.0.0.1:7890；
# requests 见到 HTTPS_PROXY 后所有请求都先打代理进程，代理进程一关连接就 refused。
# 浏览器能"挂代理还能上国内网"是因为 Clash 有智能分流规则；requests 没有，只会盲目打 7890。
# NO_PROXY=hf-mirror.com → requests 对这个域名直连，不走代理，代理开/关都能正常下载。
#
# 同时写 NO_PROXY 和 no_proxy：不同 OS / Python 库对环境变量大小写处理不同，
# Linux/Mac 区分大小写，某些库只读小写，某些只读大写，两个都设保证覆盖所有情况。
_mirror_host = "hf-mirror.com"
for _key in ("NO_PROXY", "no_proxy"):
    _existing = os.environ.get(_key, "")
    if _mirror_host not in _existing:
        os.environ[_key] = f"{_existing},{_mirror_host}" if _existing else _mirror_host
