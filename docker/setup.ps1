param([string]$GroqKeyFile)
$ErrorActionPreference = 'Stop'
$dockerConfigPath = Join-Path $PSScriptRoot '.env'
if (Test-Path -LiteralPath $dockerConfigPath) {
    Write-Output 'docker/.env already exists and was left unchanged.'
    exit 0
}

function New-PrivatePassword {
    $randomBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
    return -join ($randomBytes | ForEach-Object { $_.ToString('x2') })
}

$dockerConfig = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '.env.example'))
$dockerConfig = $dockerConfig.Replace('REPLACE_ADMIN_PASSWORD', (New-PrivatePassword))
$dockerConfig = $dockerConfig.Replace('REPLACE_APP_PASSWORD', (New-PrivatePassword))
if ($GroqKeyFile) {
    $keyItem = Get-Item -LiteralPath $GroqKeyFile
    if ($keyItem.PSIsContainer) { throw 'GroqKeyFile must identify a file.' }
    $keyPath = $keyItem.FullName.Replace('\', '/').Replace("'", "\'")
    $dockerConfig = $dockerConfig.Replace('DOCKER_GROQ_KEY_FILE=', "DOCKER_GROQ_KEY_FILE='$keyPath'")
}
$stream = [IO.File]::Open($dockerConfigPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $writer = New-Object IO.StreamWriter($stream, (New-Object Text.UTF8Encoding($false)))
    try { $writer.Write($dockerConfig) } finally { $writer.Dispose() }
} finally { $stream.Dispose() }
Write-Output 'Created ignored docker/.env with separate random database passwords.'
if ($GroqKeyFile) { Write-Output 'Linked the private Groq key file; key contents were not copied.' }
