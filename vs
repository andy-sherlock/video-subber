#!/bin/bash
# Video Subber 版本管理快捷工具
# 用法：
#   vs 迭代 "修了什么"    — 保存当前修改
#   vs 版本 v2.09        — 给当前状态打版本标签
#   vs 监控              — 查看当前改了什么
#   vs 历史              — 查看所有版本历史
#   vs 回退              — 回到上一次保存的状态

cd /Users/andy/Andy-agent/video-subtitle

case "$1" in
  迭代)
    if [ -z "$2" ]; then
      echo "请说明改了什么，比如：vs 迭代 \"修了字幕问题\""
      exit 1
    fi
    git add v2/backend v2/frontend v2/desktop.py problems.md video-subtitle.md
    git commit -m "$2"
    echo "✅ 已保存：$2"
    ;;
  版本)
    if [ -z "$2" ]; then
      echo "请指定版本号，比如：vs 版本 v2.09"
      exit 1
    fi
    git tag "$2"
    echo "✅ 版本标签已创建：$2"
    ;;
  监控)
    echo "=== 当前修改 ==="
    git status --short
    echo ""
    echo "=== 改动详情 ==="
    git diff --stat
    ;;
  历史)
    echo "=== 版本历史 ==="
    git log --oneline --decorate -20
    echo ""
    echo "=== 版本标签 ==="
    git tag -l
    ;;
  回退)
    echo "⚠️  将撤销所有未保存的修改，回到上次保存的状态。"
    read -p "确定吗？(y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
      git checkout -- .
      echo "✅ 已回退"
    else
      echo "取消"
    fi
    ;;
  *)
    echo "Video Subber 版本管理工具"
    echo ""
    echo "  vs 迭代 \"改了什么\"    保存修改"
    echo "  vs 版本 v2.09        打版本标签"
    echo "  vs 监控              看改了什么"
    echo "  vs 历史              看版本历史"
    echo "  vs 回退              撤销未保存的修改"
    ;;
esac
