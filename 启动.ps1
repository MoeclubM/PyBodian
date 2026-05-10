param(
    [string]$Action = 'menu'
)

$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$PSDefaultParameterValues['Set-Content:Encoding'] = 'utf8'
$PSDefaultParameterValues['Add-Content:Encoding'] = 'utf8'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$cli = Join-Path $root 'bodian_cli.py'
$gui = Join-Path $root 'bodian_ui.py'
$authFile = Join-Path $root '.bodian\auth.json'
$pythonArgs = @('-3.11')
$nonInteractive = $Action -ne 'menu'

function Pause-Return {
    if ($nonInteractive) {
        return
    }
    Write-Host ''
    [void](Read-Host '按回车继续')
}

function Get-AuthData {
    if (-not (Test-Path $authFile)) {
        return $null
    }

    try {
        $auth = Get-Content -Raw -Encoding utf8 $authFile | ConvertFrom-Json
        if ([string]::IsNullOrWhiteSpace($auth.uid) -or [string]::IsNullOrWhiteSpace($auth.token)) {
            return $null
        }
        return $auth
    } catch {
        return $null
    }
}

function Get-MaskedToken([string]$Token) {
    if ([string]::IsNullOrWhiteSpace($Token)) {
        return ''
    }
    if ($Token.Length -le 10) {
        return $Token
    }
    return '{0}...{1}' -f $Token.Substring(0, 6), $Token.Substring($Token.Length - 4)
}

function Show-AuthSummary {
    $auth = Get-AuthData
    if (-not $auth) {
        Write-Host '本地凭证    : 未找到可用的 .bodian\auth.json'
        return
    }

    Write-Host '本地凭证    : 已缓存'
    if (-not [string]::IsNullOrWhiteSpace($auth.nickname)) {
        Write-Host ('昵称        : {0}' -f $auth.nickname)
    }
    Write-Host ('UID         : {0}' -f $auth.uid)
    Write-Host ('Token       : {0}' -f (Get-MaskedToken $auth.token))
}

function Run-PythonCommand([string[]]$CommandArgs) {
    & py @pythonArgs @CommandArgs
}

function Show-CredentialHelp {
    Clear-Host
    Write-Host '=================================================='
    Write-Host '              UID 和 Token 是什么'
    Write-Host '=================================================='
    Write-Host 'UID:'
    Write-Host '  UID 是你的波点账号唯一标识，用来说明这个登录态属于哪个账号。'
    Write-Host ''
    Write-Host 'Token:'
    Write-Host '  Token 是当前登录态的凭证，用来证明你已经登录过。'
    Write-Host ''
    Write-Host '它们一起怎么用:'
    Write-Host '  py -3.11 bodian_cli.py login --uid UID --token TOKEN'
    Write-Host '  这条命令会把账号标识和登录凭证一起写回本地，'
    Write-Host '  让 CLI 和 GUI 以后都能直接以这个账号身份访问接口。'
    Write-Host ''
    Write-Host '为什么要保密:'
    Write-Host '  如果别人拿到了你的 UID 和 Token，就可能在 Token 失效前'
    Write-Host '  直接冒用你的登录态。所以 auth.json 和控制台输出都不建议外传。'
    Write-Host ''
    Write-Host '现在脚本会自动读取:'
    Write-Host ('  {0}' -f $authFile)
    Write-Host '  并在菜单里只显示掩码后的 Token，减少误泄露风险。'
    Pause-Return
}

function Start-Gui {
    Clear-Host
    Write-Host '正在启动 GUI...'
    try {
        & py @pythonArgs $gui
    } finally {
        taskkill /IM ffplay.exe /T /F *> $null
    }
    $code = $LASTEXITCODE
    Write-Host 'GUI 已退出。'
    if ($nonInteractive) {
        exit $code
    }
    Pause-Return
}

if ($nonInteractive) {
    switch ($Action) {
        'gui' {
            Start-Gui
            exit $LASTEXITCODE
        }
        'summary' {
            Show-AuthSummary
            exit 0
        }
        'help' {
            Show-CredentialHelp
            exit 0
        }
        'extract' {
            Run-PythonCommand @($cli, 'login', '--extract')
            exit $LASTEXITCODE
        }
        'loginSaved' {
            $auth = Get-AuthData
            if (-not $auth) {
                Write-Host '未找到可用的本地凭证。'
                exit 1
            }
            Run-PythonCommand @($cli, 'login', '--uid', [string]$auth.uid, '--token', [string]$auth.token)
            exit $LASTEXITCODE
        }
        'auth' {
            Run-PythonCommand @($cli, 'auth')
            exit $LASTEXITCODE
        }
        default {
            Write-Host ('未知动作: {0}' -f $Action)
            exit 1
        }
    }
}

while ($true) {
    Clear-Host
    Write-Host '=================================================='
    Write-Host '               PyBodian 启动菜单'
    Write-Host '=================================================='
    Write-Host 'Python      : py -3.11'
    Write-Host ('当前目录    : {0}' -f $root)
    Write-Host ''
    Show-AuthSummary
    Write-Host ''
    Write-Host '1. 从波点客户端自动提取凭证'
    Write-Host '2. 自动读取本地 UID 和 Token 并登录'
    Write-Host '3. 查看当前登录状态'
    Write-Host '4. 启动 GUI'
    Write-Host '5. 说明 UID 和 Token 的作用'
    Write-Host '0. 退出'
    Write-Host ''

    $choice = Read-Host '请输入数字并回车'

    switch ($choice) {
        '1' {
            Clear-Host
            Write-Host '正在尝试从波点客户端提取凭证...'
            Write-Host ''
            Run-PythonCommand @($cli, 'login', '--extract')
            Pause-Return
        }
        '2' {
            $auth = Get-AuthData
            Clear-Host
            if (-not $auth) {
                Write-Host '未找到可用的本地凭证。'
                Write-Host '请先执行 1. 从波点客户端自动提取凭证。'
                Pause-Return
                continue
            }
            Write-Host '正在使用本地凭证登录...'
            Write-Host ('UID   : {0}' -f $auth.uid)
            Write-Host ('Token : {0}' -f (Get-MaskedToken $auth.token))
            Write-Host ''
            Run-PythonCommand @($cli, 'login', '--uid', [string]$auth.uid, '--token', [string]$auth.token)
            Pause-Return
        }
        '3' {
            Clear-Host
            Write-Host '正在检查当前登录状态...'
            Write-Host ''
            Run-PythonCommand @($cli, 'auth')
            Pause-Return
        }
        '4' {
            Start-Gui
        }
        '5' {
            Show-CredentialHelp
        }
        '0' {
            break
        }
        default {
            Write-Host ''
            Write-Host '输入无效，请输入 0 到 5 之间的数字。'
            Pause-Return
        }
    }
}
