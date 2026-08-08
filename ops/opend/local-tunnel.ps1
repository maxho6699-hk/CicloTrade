$ErrorActionPreference = "Stop"

# Temporary bridge while the server account is rate-limited. The server-side
# listener is loopback-only; OpenD is never exposed to the public internet.
$server = "8.210.21.43"
$rootPasswordPath = Join-Path $env:USERPROFILE "Desktop\55.txt"
$serverPort = 11112
$localOpenDPort = 11111

if (-not (Test-Path -LiteralPath $rootPasswordPath -PathType Leaf)) {
    throw "找不到 $rootPasswordPath"
}

$sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
$keyPath = Join-Path $env:USERPROFILE ".ssh\ciclotrade_opend_tunnel"
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    throw "找不到隧道密钥 $keyPath"
}

if (-not (Get-NetTCPConnection -State Listen -LocalPort $localOpenDPort -ErrorAction SilentlyContinue)) {
    throw "本机 OpenD 尚未监听 127.0.0.1:$localOpenDPort，请先在 OpenD 图形界面完成登录。"
}

$target = "ciclotrade-tunnel@$server"
$forward = "127.0.0.1:${serverPort}:127.0.0.1:${localOpenDPort}"
$sshArguments = @(
    "-N",
    "-T",
    "-i", $keyPath,
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-o", "StrictHostKeyChecking=accept-new",
    "-R", $forward,
    $target
)

Write-Host "正在建立临时 OpenD 隧道..." -ForegroundColor Cyan
& $sshPath @sshArguments
if ($LASTEXITCODE -ne 0) {
    throw "SSH 隧道连接失败，退出码：$LASTEXITCODE"
}
