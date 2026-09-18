$script:UniCliPython = Join-Path $PSScriptRoot 'uni.py'
function global:uni {
    & python $script:UniCliPython @args
    $uniResult = $LASTEXITCODE
    if ($uniResult -eq 0 -and $args.Count -gt 0 -and $args[0] -in @('back', 'front', 'use')) {
        $uniDestination = & python $script:UniCliPython where
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $uniDestination -PathType Container)) {
            Set-Location -LiteralPath $uniDestination
        }
    }
    $global:LASTEXITCODE = $uniResult
}
