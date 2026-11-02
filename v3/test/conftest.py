"""全体测试的公共隔离。

P512（2026-10-03）起，开始 / 继续 / 重试 / 门口检测都由后台在收到请求时读本机设置文件。
测试绝不能读到开发机上真实的设置文件（里面有真实的 API Key）：每个测试默认把设置文件
指向一个临时空位置。需要特定设置的测试自己再替换 `load_config` 或 `CONFIG_PATH` 即可。
"""

import pytest


@pytest.fixture(autouse=True)
def _never_read_the_real_settings_file(monkeypatch, tmp_path):
    from backend import config_manager

    monkeypatch.setattr(config_manager, "CONFIG_PATH", tmp_path / "_isolated_settings" / "config.json")
