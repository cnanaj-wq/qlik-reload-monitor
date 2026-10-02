param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not $Force) {
    Write-Host ""
    Write-Host "This script removes local history and generated runtime state." -ForegroundColor Yellow
    Write-Host "It can delete: .git, SQLite databases, demo data, logs, caches, build output, and local environments." -ForegroundColor Yellow
    Write-Host ""
    $answer = Read-Host "Type RESET to continue"
    if ($answer -ne "RESET") {
        Write-Host "Cancelled."
        exit 0
    }
}

$root = (Get-Location).Path
Write-Host "Cleaning: $root"

$targets = @(
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage",
    "htmlcov",
    "data",
    "demo_data",
    "logs",
    "log",
    "frontend\dist",
    "frontend\coverage",
    "frontend\node_modules",
    "backend\.venv",
    ".venv"
)

foreach ($target in $targets) {
    $path = Join-Path $root $target
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
        Write-Host "Removed $target"
    }
}

Get-ChildItem -Path $root -Recurse -Force -File -ErrorAction SilentlyContinue |
    Where-Object {
        $_.Extension -in @(".db",".sqlite",".sqlite3",".log",".pyc") -or
        $_.Name -in @(".DS_Store","Thumbs.db")
    } |
    ForEach-Object {
        Remove-Item -Force $_.FullName
        Write-Host "Removed $($_.FullName)"
    }

Get-ChildItem -Path $root -Recurse -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "__pycache__" } |
    Sort-Object FullName -Descending |
    ForEach-Object {
        Remove-Item -Recurse -Force $_.FullName
        Write-Host "Removed $($_.FullName)"
    }

if (Test-Path ".env") {
    Remove-Item -Force ".env"
    Write-Host "Removed .env"
}

Write-Host ""
Write-Host "Local residual history and generated state removed." -ForegroundColor Green
Write-Host "The project source files remain in place."
Write-Host ""
Write-Host "Next recommended steps:"
Write-Host "  1. Review .gitignore"
Write-Host "  2. Recreate the Python environment"
Write-Host "  3. Reinstall frontend dependencies"
Write-Host "  4. Run tests"
Write-Host "  5. git init"
Write-Host "  6. Create a clean initial commit"
