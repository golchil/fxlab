<#
.SYNOPSIS
    FXLab CLI wrapper for Windows (PowerShell)

.DESCRIPTION
    Provides easy access to FXLab docker compose commands from Windows.
    Calls WSL Ubuntu to run docker compose in ~/fxlab.

.EXAMPLE
    .\fxlab.ps1 up      # Start all services
    .\fxlab.ps1 down    # Stop all services
    .\fxlab.ps1 logs    # View logs
    .\fxlab.ps1 ps      # Show running containers
    .\fxlab.ps1 open    # Open UI in browser

.NOTES
    Requires WSL with Ubuntu distribution installed.
    Docker must be running in WSL.
#>

param(
    [Parameter(Position=0)]
    [ValidateSet("up", "down", "logs", "ps", "open", "restart", "build", "help")]
    [string]$Command = "help"
)

$WSL_DISTRO = "Ubuntu"
$PROJECT_DIR = "~/fxlab"

function Invoke-FXLabCommand {
    param([string]$DockerCmd)
    wsl -d $WSL_DISTRO -- bash -lc "cd $PROJECT_DIR && docker compose $DockerCmd"
}

switch ($Command) {
    "up" {
        Write-Host "Starting FXLab services..." -ForegroundColor Cyan
        Invoke-FXLabCommand "up -d"
        Write-Host ""
        Write-Host "FXLab is running at: http://localhost:18000/ui" -ForegroundColor Green
    }
    "down" {
        Write-Host "Stopping FXLab services..." -ForegroundColor Yellow
        Invoke-FXLabCommand "down"
    }
    "logs" {
        Write-Host "Showing FXLab logs (Ctrl+C to exit)..." -ForegroundColor Cyan
        Invoke-FXLabCommand "logs -f"
    }
    "ps" {
        Write-Host "FXLab container status:" -ForegroundColor Cyan
        Invoke-FXLabCommand "ps"
    }
    "open" {
        Write-Host "Opening FXLab UI in browser..." -ForegroundColor Cyan
        Start-Process "http://localhost:18000/ui"
    }
    "restart" {
        Write-Host "Restarting FXLab services..." -ForegroundColor Yellow
        Invoke-FXLabCommand "restart"
    }
    "build" {
        Write-Host "Rebuilding FXLab containers..." -ForegroundColor Cyan
        Invoke-FXLabCommand "build"
    }
    "help" {
        Write-Host @"
FXLab CLI - Windows PowerShell wrapper

Usage: .\fxlab.ps1 <command>

Commands:
  up        Start all services (detached)
  down      Stop all services
  logs      Follow container logs
  ps        Show container status
  open      Open UI in default browser
  restart   Restart all services
  build     Rebuild containers
  help      Show this help message

Examples:
  .\fxlab.ps1 up          # Start FXLab
  .\fxlab.ps1 logs        # View logs
  .\fxlab.ps1 open        # Open browser

UI URL: http://localhost:18000/ui
API Docs: http://localhost:18000/docs
"@ -ForegroundColor White
    }
}
