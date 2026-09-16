param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$repoPath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $repoPath '.venv\Scripts\python.exe'
$url = 'http://127.0.0.1:8435'
$ready = $false
try {
    $state = Invoke-RestMethod "$url/api/state" -TimeoutSec 2
    $ready = $null -ne $state.csrf -and $null -ne $state.orders
} catch {}
if (-not $ready) {
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Order Desk Python environment is missing.' }
    Start-Process -FilePath $pythonPath -ArgumentList '-m','korea_ecommerce_mcp.orders_web' -WorkingDirectory $repoPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $repoPath 'orders-ui.stdout.log') -RedirectStandardError (Join-Path $repoPath 'orders-ui.stderr.log') | Out-Null
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $state = Invoke-RestMethod "$url/api/state" -TimeoutSec 2
            if ($null -ne $state.csrf) { $ready = $true; break }
        } catch {}
    }
}
if (-not $ready) { throw 'Could not start Order Desk. Check orders-ui.stderr.log in the installation folder.' }
if (-not $NoBrowser) { Start-Process $url }
Write-Output "Order Desk ready: $url"
