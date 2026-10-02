# MC 面板欢迎信息
if [ -z "$MCPANEL_SHOWN" ]; then
  export MCPANEL_SHOWN=1
  IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo ""
  echo "  🎮 MC 服务器面板 (MCPanelOS)"
  echo "     网页面板： http://${IP:-127.0.0.1}:8850/"
  echo "     开服向导： sudo mcpanel wizard"
  echo "     查看状态： sudo mcpanel status"
  echo "     桌面窗口： sudo mcpanel desktop   (需要图形环境)"
  echo ""
fi
