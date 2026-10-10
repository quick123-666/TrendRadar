# TrendRadar 本地定时运行脚本 (替代失效的 GitHub Actions cron)
# 由 Mavis cron 任务「TrendRadar 热点监控」调用 (原计划任务方案未启用)
#
# 用法:
#   .\run_trendradar.ps1           # 只抓取, 生成报告
#   .\run_trendradar.ps1 -Open     # 抓完后自动用默认浏览器打开历史索引页
param(
    [switch]$Open   # 跑完自动打开 output\_index.html
)

$ErrorActionPreference = 'Continue'

# 1. 关键: 强制 UTF-8。不设的话 Windows 默认 GBK,
#    程序打印 ✅/❌ 和中文会抛 UnicodeEncodeError 直接崩。
#    2026-10-10 实测踩过: UnicodeEncodeError: 'gbk' codec can't encode '\u2705'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

# 2. 本地跑, 不是 CI
$env:GITHUB_ACTIONS = 'false'

# 3. 目标目录
$ProjectDir = 'C:\Users\Administrator\Desktop\TrendRadar'
if (-not (Test-Path $ProjectDir)) {
    Write-Output "[ERROR] 目录不存在: $ProjectDir"
    exit 1
}
Set-Location $ProjectDir

# 4. 从【用户级】环境变量补齐缺失的变量 (2026-10-10 修复)
#
#    症状: 日志报 "未配置 AI API Key", 但用户级明明 AI_API_KEY=SET。
#    原因: 当前 shell 进程是在设置该变量【之前】启动的, 不会自动继承。
#          Process 级为空 → TrendRadar 读不到 → AI 分析静默失败。
#    修法: 启动时显式从 User 作用域捞一遍, 只补不覆盖。
#
#    这样无论从 Mavis cron / 计划任务 / 手动开新终端进来, 行为都一致。
foreach ($n in @('AI_API_KEY', 'AI_API_BASE', 'AI_MODEL', 'MINIMAX_API_KEY')) {
    $cur = [Environment]::GetEnvironmentVariable($n, 'Process')
    if (-not $cur) {
        $usr = [Environment]::GetEnvironmentVariable($n, 'User')
        if ($usr) {
            Set-Item -Path "Env:$n" -Value $usr
            Write-Output "[env] $n 从用户级补入 (长度 $($usr.Length))"
        }
    }
}
if (-not $env:AI_API_KEY) {
    Write-Output "[WARN] AI_API_KEY 仍为空 —— AI 分析会跳过 (报告仍会正常生成)"
}

# 5. 通知功能已在 config/config.yaml 里整体关闭 (notification.enabled: false)
#    用户 2026-10-10 决定: 只在本地看 HTML, 不推任何外部渠道。
#    如果以后想开, 改回 true 并配 webhook —— 切记 key 只走环境变量, 不要写进 config.yaml
#    (仓库是 public)。

$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Output "=========================================="
Write-Output "TrendRadar 启动: $stamp"
Write-Output "Python: $(& uv run python --version 2>&1)"
Write-Output "=========================================="

& uv run python -m trendradar
$code = $LASTEXITCODE

# 6. 抓完自动刷新本地历史索引页 (output/_index.html)
#    把历次报告 / 数据库 / 关键词命中汇总成一个可点击的页面
Write-Output ""
Write-Output "--- 刷新本地历史索引 ---"
& uv run python make_history_index.py
$idxCode = $LASTEXITCODE
$IndexFile = Join-Path $ProjectDir 'output\_index.html'
if (Test-Path $IndexFile) {
    Write-Output "历史索引: $IndexFile"
    if ($Open) {
        Start-Process $IndexFile
        Write-Output "已用默认浏览器打开"
    }
} else {
    Write-Output "历史索引生成失败 (退出码 $idxCode)"
}

Write-Output "=========================================="
Write-Output "TrendRadar 结束: $(Get-Date -Format 'yyyy-MM-dd %H:%M:%S')  退出码=$code"
Write-Output "=========================================="
exit $code
