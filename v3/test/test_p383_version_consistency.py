"""P383: 版本号防分叉闸门（后端两段式 ↔ 桌面壳三段式，对应同一版本）。

项目历史用「3.0X」两段式命名（3.01…3.08→3.09），Python 后端可原样显示给用户；
桌面壳 Cargo/Tauri 受 Rust semver 限制（前导零非法，实测 `3.09` 会 cargo build 失败），
只能写等价三段式 `3.0.9`。故三处不字面相等，但必须对应同一版本：
后端 `3.{XX}` ↔ 桌面壳 `3.0.{int(XX)}`。本测试锁定该对应 + 桌面壳内部一致。
"""
import json
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent


class VersionConsistencyTest(unittest.TestCase):
    def test_backend_and_desktop_shell_agree_on_version(self):
        with open(ROOT / "pyproject.toml", "rb") as f:
            py_ver = tomllib.load(f)["project"]["version"]
        with open(ROOT / "src-tauri" / "Cargo.toml", "rb") as f:
            cargo_ver = tomllib.load(f)["package"]["version"]
        with open(ROOT / "src-tauri" / "tauri.conf.json", encoding="utf-8") as f:
            tauri_ver = json.load(f)["version"]
        # 桌面壳两处都是 Rust/Tauri 元数据，必须字面一致
        self.assertEqual(cargo_ver, tauri_ver,
                         f"桌面壳 Cargo={cargo_ver} 与 tauri={tauri_ver} 分叉")
        # 后端两段式「3.XX」应对应桌面壳三段式「3.0.{int(XX)}」
        parts = py_ver.split(".")
        self.assertEqual(len(parts), 2,
                         f"后端版本应为两段式 3.XX，实际 {py_ver}")
        expected_shell = f"{parts[0]}.0.{int(parts[1])}"
        self.assertEqual(cargo_ver, expected_shell,
                         f"后端 {py_ver} 应对应桌面壳 {expected_shell}，实际 {cargo_ver}")


if __name__ == "__main__":
    unittest.main()
