# Run from the repository root, in PowerShell 5.1+.
# Example:
#   .\evaluation\scalability.ps1 -Workers 2 -RunId a1
#   .\evaluation\scalability.ps1 -Workers 4 -RunId b1
# CAUTION: Stops/restarts the local Compose worker service and inserts test data.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet(2,4)]
    [int]$Workers,
    [Parameter(Mandatory=$true)]
    [ValidatePattern('^[a-zA-Z0-9_-]{1,40}$')]
    [string]$RunId,
    [ValidateRange(20,2000)]
    [int]$ResourceCount = 200,
    [ValidateRange(0,5000)]
    [int]$DelayMs = 500,
    [ValidateRange(10,180)]
    [int]$LeadSeconds = 45,
    [ValidateRange(30,1200)]
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
$course = "eval-scale-$Workers-$RunId"

if (-not (Test-Path 'docker-compose.yml') -and -not (Test-Path 'compose.yaml') -and -not (Test-Path 'compose.yml')) {
    throw 'Run this script from the EduPulse repository root.'
}

function Invoke-Psql([string]$Sql, [switch]$Terse) {
    $args = @('compose','exec','-T','postgres','psql','-v','ON_ERROR_STOP=1','-U','edupulse','-d','edupulse')
    if ($Terse) { $args += @('-A','-t') }
    $args += @('-c',$Sql)
    $out = & docker @args
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL command failed: $Sql" }
    return ($out -join "`n")
}

Write-Host "Experiment: $course; workers=$Workers; resources=$ResourceCount; delay_ms=$DelayMs"
Write-Host 'Stopping current local Worker replicas to isolate the test...'
& docker compose stop worker
if ($LASTEXITCODE -ne 0) { throw 'Failed to stop local Workers.' }

# One course per run prevents the benchmark from including older observations.
# Use a UTC-compatible timestamptz from PostgreSQL; do not depend on local clock.
$setup = @"
BEGIN;
INSERT INTO courses (id, name) VALUES ('$course', 'Scalability Evaluation ($Workers workers)');
INSERT INTO resources (course_id, name, url, check_interval_seconds, next_check_at)
SELECT '$course', 'Scale Resource ' || i,
       'http://fake-site:8000/load/' || i || '?delay_ms=$DelayMs',
       3600, NOW() + INTERVAL '$LeadSeconds seconds'
FROM generate_series(1, $ResourceCount) i;
COMMIT;
"@
# If the course already exists, INSERT will fail safely; use a new RunId.
[void](Invoke-Psql $setup)
$due = (Invoke-Psql "SELECT MIN(next_check_at)::text FROM resources WHERE course_id = '$course';" -Terse).Trim()
$count = (Invoke-Psql "SELECT COUNT(*) FROM resources WHERE course_id = '$course';" -Terse).Trim()
if ([int]$count -ne $ResourceCount) { throw "Expected $ResourceCount resources; got $count" }
Write-Host "Common scheduled due timestamp: $due"

# Record timestamp before starting workers for transparent timing context.
Write-Host "Starting $Workers Worker replicas..."
& docker compose up -d --scale "worker=$Workers" worker
if ($LASTEXITCODE -ne 0) { throw 'Failed to start Workers.' }

try {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $completed = 0
    do {
        $completed = [int](Invoke-Psql "SELECT COUNT(DISTINCT cr.resource_id) FROM check_results cr JOIN resources r ON r.id=cr.resource_id WHERE r.course_id='$course';" -Terse).Trim()
        Write-Host "Progress: $completed / $ResourceCount"
        if ($completed -eq $ResourceCount) { break }
        if ($completed -gt $ResourceCount) { throw 'Unexpected resource count.' }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    if ($completed -ne $ResourceCount) { throw "Timed out at $completed / $ResourceCount completed" }
} finally {
    Write-Host 'Stopping benchmark Worker replicas...'
    & docker compose stop worker
}

$sql = @"
SELECT COUNT(*) AS results,
       COUNT(DISTINCT cr.resource_id) AS unique_resources,
       ROUND(EXTRACT(EPOCH FROM MAX(cr.checked_at) - '$due'::timestamptz)::numeric, 3) AS completion_seconds,
       ROUND((COUNT(*) / NULLIF(EXTRACT(EPOCH FROM MAX(cr.checked_at) - '$due'::timestamptz), 0))::numeric, 3) AS checks_per_second,
       ROUND(AVG(EXTRACT(EPOCH FROM cr.checked_at - '$due'::timestamptz) * 1000)::numeric, 2) AS avg_due_to_result_ms,
       ROUND(AVG(cr.latency_ms)::numeric, 2) AS avg_http_latency_ms
FROM check_results cr JOIN resources r ON r.id=cr.resource_id
WHERE r.course_id='$course';
"@
Write-Host "`n=== Results ($course) ==="
Write-Host (Invoke-Psql $sql)
Write-Host 'Note: due-to-result delay includes queuing, HTTP execution, and DB persistence (not pure queue lag).'
Write-Host 'Record the output and repeat with a distinct RunId and the other worker count.'
