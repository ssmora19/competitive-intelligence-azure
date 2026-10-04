# procesar_todo.ps1
# QUE HACE: recorre un contenedor Bronze completo llamando a la capa Silver por tandas.
# PARA QUE SIRVE: procesar miles de blobs sin cambiar la URL a mano ni chocar el timeout.

param(
    [string]$Contenedor = "bronze-competidores",  # contenedor Bronze a procesar
    [int]$Tanda = 200,                            # tamano de cada tanda
    [int]$Total = 3000                            # tope superior (mayor o igual al total real)
)

$base = "http://localhost:7071/api/silver/procesar"
$desde = 0
$acumEscritos = 0
$acumExistente = 0
$acumSinFamilia = 0
$acumErrores = 0

Write-Host "Procesando '$Contenedor' en tandas de $Tanda ..." -ForegroundColor Cyan

while ($desde -lt $Total) {
    $hasta = $desde + $Tanda
    $url = "$base`?contenedor=$Contenedor&desde=$desde&hasta=$hasta"

    try {
        $r = Invoke-RestMethod -Uri $url -Method Get -TimeoutSec 600
    }
    catch {
        Write-Host "ERROR en tanda $desde-$hasta : $_" -ForegroundColor Red
        break
    }

    # Si ya no hay blobs en el rango, terminamos.
    if ($r.en_tanda -eq 0) {
        Write-Host "Fin: no hay mas blobs despues de $desde." -ForegroundColor Yellow
        break
    }

    $acumEscritos   += $r.escritos
    $acumExistente  += $r.skip_existente
    $acumSinFamilia += $r.sin_familia
    $acumErrores    += $r.errores

    $color = if ($r.errores -gt 0) { "Red" } else { "Green" }
    Write-Host ("[{0,5}-{1,-5}] escritos={2,-4} ya_existian={3,-4} sin_familia={4,-3} errores={5}" -f `
        $desde, $hasta, $r.escritos, $r.skip_existente, $r.sin_familia, $r.errores) -ForegroundColor $color

    if ($r.errores -gt 0) {
        Write-Host "  Detalle: $($r.detalle_errores | ConvertTo-Json -Compress)" -ForegroundColor Red
    }

    $desde = $hasta
}

Write-Host "`n===== RESUMEN '$Contenedor' =====" -ForegroundColor Cyan
Write-Host "  Escritos totales : $acumEscritos"
Write-Host "  Ya existian      : $acumExistente"
Write-Host "  Sin familia      : $acumSinFamilia"
Write-Host "  Errores          : $acumErrores"