$ErrorActionPreference = "Stop"

# The tunnel keeps OpenD's Telnet control port on loopback; it is never exposed publicly.
$server = "8.210.21.43"
$rootPasswordPath = Join-Path $env:USERPROFILE "Desktop\55.txt"
$captchaRemotePath = "/opt/opend/.com.futunn.FutuOpenD/F3CNN/PicVerifyCode.png"
$localPort = 33322

if (-not (Test-Path -LiteralPath $rootPasswordPath -PathType Leaf)) {
    throw "找不到 $rootPasswordPath"
}

Import-Module Posh-SSH
$rootPassword = (Get-Content -Raw -LiteralPath $rootPasswordPath).Trim()
$securePassword = ConvertTo-SecureString $rootPassword -AsPlainText -Force
$credential = [pscredential]::new("root", $securePassword)
$session = $null

try {
    $session = New-SSHSession -ComputerName $server -Credential $credential -AcceptKey -ConnectionTimeout 20 -ErrorAction Stop
    New-SSHLocalPortForward -SessionId $session.SessionId -BoundHost "127.0.0.1" -BoundPort $localPort -RemoteAddress "127.0.0.1" -RemotePort 22222 | Out-Null

    # OpenD may keep returning a previously downloaded image. Move it aside so
    # this run cannot continue until a genuinely new captcha has been written.
    $captchaStalePath = "$captchaRemotePath.stale-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    $prepareCommand = 'if [ -f "' + $captchaRemotePath + '" ]; then mv -- "' + $captchaRemotePath + '" "' + $captchaStalePath + '"; fi'
    $prepareResult = Invoke-SSHCommand -SessionId $session.SessionId -Command $prepareCommand
    if ($prepareResult.ExitStatus -ne 0) {
        throw "无法清理旧验证码图片。"
    }

    $requestClient = [Net.Sockets.TcpClient]::new()
    $requestClient.Connect("127.0.0.1", $localPort)
    try {
        $requestStream = $requestClient.GetStream()
        $requestStream.ReadTimeout = 3000
        $requestBuffer = New-Object byte[] 4096
        try { [void]$requestStream.Read($requestBuffer, 0, $requestBuffer.Length) } catch [System.IO.IOException] { }
        $requestPayload = [Text.Encoding]::UTF8.GetBytes("req_pic_verify_code`r`n")
        $requestStream.Write($requestPayload, 0, $requestPayload.Length)
        Start-Sleep -Milliseconds 500
    } finally {
        $requestClient.Dispose()
    }

    $captchaReady = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $checkCommand = 'test -s "' + $captchaRemotePath + '"'
        $checkResult = Invoke-SSHCommand -SessionId $session.SessionId -Command $checkCommand
        if ($checkResult.ExitStatus -eq 0) {
            $captchaReady = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }
    if (-not $captchaReady) {
        throw "OpenD 未生成新的验证码图片，请确认服务仍在等待图形验证。"
    }

    $captchaName = "ciclotrade-opend-captcha-{0}.png" -f (Get-Date -Format "yyyyMMdd-HHmmss")
    Get-SCPItem -ComputerName $server -Credential $credential -AcceptKey -Path $captchaRemotePath -PathType File -Destination $env:TEMP -NewName $captchaName -Force
    $captchaPath = Join-Path $env:TEMP $captchaName
    Start-Process $captchaPath

    Write-Host "验证码图片已打开：$captchaPath" -ForegroundColor Cyan
    $code = (Read-Host "请输入验证码（区分大小写）").Trim()
    if (-not $code -or $code.Length -gt 16 -or $code -notmatch "^[A-Za-z0-9]+$") {
        throw "验证码格式无效。"
    }

    $client = [Net.Sockets.TcpClient]::new()
    $client.Connect("127.0.0.1", $localPort)
    try {
        $stream = $client.GetStream()
        $stream.ReadTimeout = 3000
        $buffer = New-Object byte[] 4096
        try { [void]$stream.Read($buffer, 0, $buffer.Length) } catch [System.IO.IOException] { }
        $payload = [Text.Encoding]::UTF8.GetBytes("input_pic_verify_code -code=$code`r`n")
        $stream.Write($payload, 0, $payload.Length)
        Start-Sleep -Milliseconds 800
        $response = ""
        try {
            $count = $stream.Read($buffer, 0, $buffer.Length)
            if ($count -gt 0) { $response = [Text.Encoding]::UTF8.GetString($buffer, 0, $count) }
        } catch [System.IO.IOException] { }
        if ($response) { Write-Host $response.Trim() }
        if ($response -match "错误|失败") {
            throw "OpenD 未接受验证码，请重新请求验证码后再试。"
        }
        Write-Host "验证码已提交。等待 OpenD 登录状态确认。" -ForegroundColor Green
    } finally {
        $client.Dispose()
    }
} finally {
    if ($session) {
        Stop-SSHPortForward -SessionId $session.SessionId -BoundHost "127.0.0.1" -BoundPort $localPort -ErrorAction SilentlyContinue
        Remove-SSHSession -SessionId $session.SessionId | Out-Null
    }
}
