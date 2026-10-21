# Asks for your Cloudflare API token without showing it, and keeps it only in
# this terminal window (it is never written to a file). Run it with:
#     . .\worker\set-token.ps1
$secure = Read-Host "Paste your Cloudflare API token (it won't show)" -AsSecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:CLOUDFLARE_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
}
if ($env:CLOUDFLARE_API_TOKEN) { Write-Host "Token set for this terminal window only." } else { Write-Host "No token entered." }
