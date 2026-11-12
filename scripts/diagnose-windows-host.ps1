param(
    [int[]]$Ports = @(80, 443, 3000, 5000, 8000, 8080)
)

$ErrorActionPreference = 'Stop'
$services = Get-CimInstance Win32_Service | Where-Object { $_.ProcessId -gt 0 }
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $Ports -contains $_.LocalPort }

$listeners | ForEach-Object {
    $listener = $_
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $service = $services | Where-Object { $_.ProcessId -eq $listener.OwningProcess } | Select-Object -First 1
    [pscustomobject]@{
        Address = $listener.LocalAddress
        Port = $listener.LocalPort
        Process = if ($process) { $process.ProcessName } else { '?' }
        WindowsService = if ($service) { $service.Name } else { '(no aparece como servicio)' }
    }
} | Sort-Object Port, Address -Unique | Format-Table -AutoSize

Write-Output 'Herramientas disponibles:'
Get-Command nssm, winsw, cloudflared, caddy, nginx, pm2 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty Name
