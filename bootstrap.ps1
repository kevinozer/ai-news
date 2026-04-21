# bootstrap.ps1 - prvni commit do github.com/kevinozer/ai-news
# Spusteni: v teto slozce pravou mysi -> Run with PowerShell
# Nebo:     powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Aby se okno po skonceni / chybe nezavrelo
trap {
    Write-Host ""
    Write-Host "CHYBA: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Kompletni error:" -ForegroundColor Red
    $_ | Format-List * -Force | Out-String | Write-Host
    Read-Host "`nStiskni Enter pro zavreni"
    exit 1
}

Write-Host "== AI News bootstrap ==" -ForegroundColor Cyan

# Kontrola, ze git je dostupny
try {
    $gitVer = (git --version) 2>&1
    Write-Host "Git: $gitVer"
} catch {
    Write-Error "Git neni nainstalovany nebo neni v PATH. Stahni Git for Windows: https://git-scm.com/download/win"
}

# 1) Cisty start - smaz pokazeny .git z predchoziho pokusu
if (Test-Path .git) {
    Write-Host "Mazu stary .git (byl v polorozbitem stavu)..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force .git
}

# 2) Init
Write-Host "git init..."
git init -b main | Out-Null
git config user.name  "Kevin Ozer"
git config user.email "kevin.ozer@seznam.cz"

# 3) Sanity check - .gitignore blokuje .env, cache, generovane posty
if (-not (Test-Path .gitignore)) {
    Write-Error ".gitignore chybi. Abort, nechci pushnout .env."
    exit 1
}
$ignoreHits = Select-String -Path .gitignore -Pattern "^\.env$" -Quiet
if (-not $ignoreHits) {
    Write-Error ".gitignore neignoruje .env. Abort."
    exit 1
}

# 4) Stage + commit
Write-Host "Stage vseho krom .gitignore listu..."
git add -A
git status --short

Write-Host "Commit..."
git commit -m "chore: initial commit - AI News pipeline (fetch + PDF + posts)" | Out-Null

# 5) Remote + push (Git for Windows otevre browser OAuth popup pri prvnim pushi)
Write-Host "Nastavuji remote na kevinozer/ai-news..."
# .git jsme cerstve init-ovali, takze origin neexistuje - jen pridame
$existingRemote = git remote 2>$null
if ($existingRemote -match "^origin$") {
    git remote set-url origin https://github.com/kevinozer/ai-news.git
} else {
    git remote add origin https://github.com/kevinozer/ai-news.git
}

Write-Host "Push na main (otevre se browser pro prihlaseni k GitHubu, pokud nejsi prihlasen)..." -ForegroundColor Cyan
# Push vypiny stderr progress jinak - $ErrorActionPreference=Stop plus git progress ~~ false alarm
$ErrorActionPreference = "Continue"
git push -u origin main
$pushExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($pushExit -ne 0) {
    Write-Error "git push selhal (exit $pushExit). Zkus rucne: git push -u origin main"
}

Write-Host ""
Write-Host "OK. Repo je pripravene." -ForegroundColor Green
Write-Host "Dal:"
Write-Host "  1. Otevri https://github.com/kevinozer/ai-news/actions a zapni workflows"
Write-Host "  2. Rucne spust fetch: Actions -> 'Daily fetch AI news' -> Run workflow"
Write-Host ""
Read-Host "Stiskni Enter pro zavreni"
