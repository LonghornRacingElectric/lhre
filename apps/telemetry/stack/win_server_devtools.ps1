function Invoke-TelemetryCompose {
    $composeArguments = @($args)
    if ((Split-Path (Get-Location).Path -Leaf) -eq "ingest") {
        $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\..\.."))
        $generated = Join-Path $repoRoot "bazel-bin\lib\codegen\telemetry\orion_db_init.sql"
        $selected = $env:ORION_DB_INIT_SQL
        if (-not $selected) { $selected = $generated }
        if ($composeArguments[0] -in @("up", "create", "run", "watch")) {
            if ($selected -ne $generated) {
                if ($selected -notmatch '^(?:[A-Za-z]:[\\/]|\\\\)' -or -not (Test-Path -LiteralPath $selected -PathType Leaf)) {
                    throw "Explicit ORION_DB_INIT_SQL must name an existing absolute file: $selected"
                }
            } else {
                Get-Command bazel -ErrorAction Stop | Out-Null
                Push-Location $repoRoot
                try {
                    & bazel build --config=local //apps/telemetry:current_schema_sql
                    if ($LASTEXITCODE -ne 0) { throw "Bazel failed to generate the Orion database schema" }
                } finally {
                    Pop-Location
                }
                if (-not (Test-Path -LiteralPath $generated -PathType Leaf)) {
                    throw "Bazel did not produce Orion database schema: $generated"
                }
            }
        }
        $env:ORION_DB_INIT_SQL = $selected
    }
    & docker-compose @composeArguments
}

# Welcome message and options
Write-Host "Welcome to the Telemetry Server CLI Devtool. Please pick from the following methods to start the server:"
Write-Host "`t1) Start the server (ONLY)"
Write-Host "`t2) Delete the existing images"
Write-Host "`t3) Delete the existing images and telemetry_db volume"
Write-Host "`t4) Delete the existing images and both volumes (INCLUDING GRAFANA DASHBOARDS!)"
Write-Host "`tQ) Run Processor in background and start server"
Write-Host "`tW) Delete the existing server and processors images"
Write-Host "`tE) Delete the lap timer processors images"
Write-Host "`tF) Delete the gps classifier processors images"
Write-Host "`tS) Stop everything currently running"
Write-Host ""

function Stop-TelemetryStack {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptRoot
    )

    $composeTargets = @(
        @{ Path = (Join-Path $ScriptRoot "ingest"); Label = "ingest services" },
        @{ Path = (Join-Path $ScriptRoot "kafka"); Label = "kafka services" },
        @{ Path = (Join-Path $ScriptRoot "processors"); Label = "processor services" },
        @{ Path = (Join-Path $ScriptRoot "processors\lap_timer"); Label = "lap timer processor" },
        @{ Path = (Join-Path $ScriptRoot "processors\gps_classifier"); Label = "gps classifier processor" },
        @{ Path = (Join-Path $ScriptRoot "processors\track_mapper"); Label = "track mapper processor" },
        @{ Path = (Join-Path $ScriptRoot "processors\kafka_test"); Label = "kafka test processor" }
    )

    foreach ($target in $composeTargets) {
        $composeFile = Join-Path $target.Path "docker-compose.yml"
        if (Test-Path $composeFile) {
            Set-Location $target.Path
            Write-Host "Stopping $($target.Label)..."
            Invoke-TelemetryCompose down
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Failed to stop $($target.Label)."
            }
        }
    }

    Write-Host "Stopped all telemetry stack services that were running."
}

# Infinite loop to process user input
while ($true) {
    $opt = Read-Host "Choose an option (1-4, Q, W, E, F, S)"
    Write-Host ""
    Write-Host ""

    # Change directory to "ingest"
    Set-Location (Get-ChildItem -Recurse -Filter "ingest" | Select-Object -First 1).FullName
    if (-not (Test-Path .\ingest))
    {
        switch ($opt)
        {
            "1" {
                # Option 1: Start the server
                Invoke-TelemetryCompose down
                Invoke-TelemetryCompose up
                break
            }
            "2" {
            # --- robustly locate sibling dirs from current (likely ...\ingest) ---
                $orig = Get-Location
                $root = $orig
                while (-not (Test-Path (Join-Path $root 'kafka')) -and (Split-Path $root -Parent) -ne $root) {
                    $root = Split-Path $root -Parent
                }
                if (-not (Test-Path (Join-Path $root 'kafka'))) {
                    Write-Host "Failed to find 'kafka' directory relative to $orig"
                    break
                }
                $kafkaDir  = Join-Path $root 'kafka'
                $ingestDir = Join-Path $root 'ingest'

                # --- kafka first ---
                Set-Location $kafkaDir
                Invoke-TelemetryCompose down
                Invoke-TelemetryCompose up -d

                # --- then ingest ---
                if (-not (Test-Path $ingestDir)) {
                    Write-Host "Failed to find 'ingest' directory relative to $orig"
                    break
                }
                Set-Location $ingestDir

                Invoke-TelemetryCompose down
                $telemetryBackendImages = docker image ls --filter "reference=telemetry_backend" -q
                foreach ($image in $telemetryBackendImages) {
                    docker rmi $image
                }
                Invoke-TelemetryCompose up
                break
            }
            "3" {
                # --- robustly locate sibling dirs from current (likely ...\ingest) ---
                $orig = Get-Location
                $root = $orig
                while (-not (Test-Path (Join-Path $root 'kafka')) -and (Split-Path $root -Parent) -ne $root) {
                    $root = Split-Path $root -Parent
                }
                if (-not (Test-Path (Join-Path $root 'kafka'))) {
                    Write-Host "Failed to find 'kafka' directory relative to $orig"
                    break
                }
                $kafkaDir  = Join-Path $root 'kafka'
                $ingestDir = Join-Path $root 'ingest'

                # --- kafka first ---
                Set-Location $kafkaDir
                Invoke-TelemetryCompose down
                Invoke-TelemetryCompose up -d

                # --- then ingest ---
                if (-not (Test-Path $ingestDir)) {
                    Write-Host "Failed to find 'ingest' directory relative to $orig"
                    break
                }
                Set-Location $ingestDir

                # Option 3: Delete images + reset telemetry_db volume
                Invoke-TelemetryCompose down
                $telemetryBackendImages = docker image ls --filter "reference=telemetry_backend" -q
                foreach ($image in $telemetryBackendImages) {
                    docker rmi $image
                }
                docker volume rm telemetry_db
                docker volume create telemetry_db
                Invoke-TelemetryCompose up
                break
            }
            "4" {
                # Option 4: Delete the existing images and both volumes (INCLUDING GRAFANA DASHBOARDS!)
                while ($true)
                {
                    $yn = Read-Host "You are about to delete the Grafana dashboards saved on this computer. Are you sure you intend to delete this? [Y/n]"
                    Write-Host ""
                    Write-Host ""

                    if ($yn -match "^[Yy]$")
                    {
                        Invoke-TelemetryCompose down
                        $telemetryBackendImages = docker image ls --filter "reference=telemetry_backend" -q
                        foreach ($image in $telemetryBackendImages)
                        {
                            docker rmi $image
                        }
                        docker volume rm telemetry_db
                        docker volume create telemetry_db
                        docker volume rm grafana_storage
                        docker volume create grafana_storage
                        Invoke-TelemetryCompose up
                        break
                    }
                    elseif ($yn -match "^[Nn]$")
                    {
                        Write-Host "Crisis Averted!"
                        break
                    }
                    else
                    {
                        Write-Host "Invalid input, please try again."
                    }
                }
                break
            }
            "Q" {
                # Option Q: Run Processor in background and start server
                Invoke-TelemetryCompose down
                Invoke-TelemetryCompose up -d
                # Change directory to "processors"
                Set-Location (Get-ChildItem -Recurse -Directory | Where-Object { $_.Name -eq "processors" }).FullName
                if (-not (Test-Path .\processors))
                {
                    Write-Host "Failed to find processors"
                    exit
                }
                Invoke-TelemetryCompose down
                Invoke-TelemetryCompose up -d
                $processorContainerId = (docker container ls --filter "name=telemetry_processors" -q)
                Write-Host "Processor container ID: $processorContainerId"
                # Change directory back to "ingest"
                Set-Location (Get-ChildItem -Recurse -Directory | Where-Object { $_.Name -eq "ingest" }).FullName
                Invoke-TelemetryCompose logs -f
                break
            }
            "W" {
                # Option W: Delete the existing server and processors images
                Invoke-TelemetryCompose down
                $telemetryBackendImages = docker image ls --filter "reference=telemetry_backend" -q
                foreach ($image in $telemetryBackendImages)
                {
                    docker rmi $image
                }
                $telemetryProcessorsImages = docker image ls --filter "reference=telemetry_processors" -q
                foreach ($image in $telemetryProcessorsImages)
                {
                    docker rmi $image
                }
                Invoke-TelemetryCompose up -d
                # Change directory to "processors"
                Set-Location (Get-ChildItem -Recurse -Directory | Where-Object { $_.Name -eq "processors" }).FullName
                if (-not (Test-Path .\processors))
                {
                    Write-Host "Failed to find processors"
                    exit
                }
                Invoke-TelemetryCompose down
                Invoke-TelemetryCompose up -d
                $processorContainerId = (docker container ls --filter "name=telemetry_processors" -q)
                Write-Host "Processor container ID: $processorContainerId"
                # Change directory back to "ingest"
                Set-Location (Get-ChildItem -Recurse -Directory | Where-Object { $_.Name -eq "ingest" }).FullName
                Invoke-TelemetryCompose logs -f
                break
            }
            "E" {
                # Change directory to processors/lap_timer
                $lapTimerPath = Get-ChildItem -Recurse -Directory | Where-Object { $_.FullName -like "*\processors\lap_timer" }
                if (-not $lapTimerPath) {
                    Write-Host "Failed to find processors/lap_timer"
                    exit
                }
                Set-Location $lapTimerPath.FullName
                Invoke-TelemetryCompose down
                $lapTimerImages = docker image ls --filter "reference=lap_timer" -q
                foreach ($image in $lapTimerImages) {
                    docker rmi $image
                }
                Invoke-TelemetryCompose up
                break
            }
            "F" {
                # Change directory to processors/gps_classifier
                $gpsClassifierPath = Get-ChildItem -Recurse -Directory | Where-Object { $_.FullName -like "*\processors\gps_classifier" }
                if (-not $gpsClassifierPath) {
                    Write-Host "Failed to find processors/gps_classifier"
                    exit
                }
                Set-Location $gpsClassifierPath.FullName
                Invoke-TelemetryCompose down
                $gpsClassifierImages = docker image ls --filter "reference=gps_classifier" -q
                foreach ($image in $gpsClassifierImages) {
                    docker rmi $image
                }
                Invoke-TelemetryCompose up
                break
            }
            "S" {
                Stop-TelemetryStack -ScriptRoot $PSScriptRoot
                break
            }
            default {
                Write-Host "Invalid input, please try again."
            }
        }
    }
}
