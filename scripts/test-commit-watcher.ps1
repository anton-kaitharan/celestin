[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$script:SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:PowerShellExe = (Get-Process -Id $PID).Path
$script:TestRoot = Join-Path ([IO.Path]::GetTempPath()) ("commit-watcher-test-" + [guid]::NewGuid().ToString("N"))
$script:FakeCodexLog = Join-Path $script:TestRoot "fake-codex.jsonl"
$script:WatcherProcesses = [Collections.Generic.List[Diagnostics.Process]]::new()

function Assert-That {
    param([bool]$Condition, [Parameter(Mandatory)][string]$Message)
    if (-not $Condition) { throw "Assertion failed: $Message" }
}

function Invoke-TestGit {
    param([Parameter(Mandatory)][string]$Repository, [Parameter(Mandatory)][string[]]$Arguments)
    $result = & git -C $Repository @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed in $Repository`: $result"
    }
    return $result
}

function New-DisposableRepository {
    param([Parameter(Mandatory)][string]$Name)

    $path = Join-Path $script:TestRoot $Name
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    Invoke-TestGit $path @("init", "--quiet") | Out-Null
    Invoke-TestGit $path @("config", "user.name", "Commit Watcher Test") | Out-Null
    Invoke-TestGit $path @("config", "user.email", "commit-watcher@example.invalid") | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $path "scripts") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $path ".codex/agents") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $script:SourceRoot "scripts/commit-watcher.ps1") -Destination (Join-Path $path "scripts/commit-watcher.ps1")
    Copy-Item -LiteralPath (Join-Path $script:SourceRoot ".codex/agents/commit-granular.toml") -Destination (Join-Path $path ".codex/agents/commit-granular.toml")
    Set-Content -LiteralPath (Join-Path $path "README.md") -Value "Baseline" -Encoding utf8
    Set-Content -LiteralPath (Join-Path $path "AGENTS.md") -Value "Run the checks for each change. Leave scratch.txt and .env uncommitted." -Encoding utf8
    Invoke-TestGit $path @("add", "README.md", "AGENTS.md", "scripts/commit-watcher.ps1", ".codex/agents/commit-granular.toml") | Out-Null
    Invoke-TestGit $path @("commit", "--quiet", "-m", "chore: initialize disposable test repository") | Out-Null
    return $path
}

function Start-WatcherProcess {
    param(
        [Parameter(Mandatory)][string]$Repository,
        [int]$QuietSeconds = 2,
        [int]$PollSeconds = 1,
        [string]$StopFile,
        [switch]$Once
    )

    $watcher = Join-Path $Repository "scripts/commit-watcher.ps1"
    $repoLabel = Split-Path -Leaf $Repository
    $stdout = Join-Path $script:TestRoot "$repoLabel.watcher.stdout.log"
    $stderr = Join-Path $script:TestRoot "$repoLabel.watcher.stderr.log"
    $arguments = "-NoProfile -File `"$watcher`" -QuietPeriodSeconds $QuietSeconds -PollIntervalSeconds $PollSeconds -CodexCommand `"$script:FakeCodex`""
    if ($Once) { $arguments += " -Once" }
    if ($StopFile) { $arguments += " -StopFilePath `"$StopFile`"" }
    $process = Start-Process -FilePath $script:PowerShellExe -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $script:WatcherProcesses.Add($process)
    return [pscustomobject]@{ Process = $process; StdOut = $stdout; StdErr = $stderr }
}

function Wait-Watcher {
    param([Parameter(Mandatory)]$Watcher, [int]$TimeoutMilliseconds = 20000)
    if (-not $Watcher.Process.WaitForExit($TimeoutMilliseconds)) {
        $Watcher.Process.Kill()
        throw "Watcher process timed out. Output: $(Get-Content -Raw $Watcher.StdOut -ErrorAction SilentlyContinue) $(Get-Content -Raw $Watcher.StdErr -ErrorAction SilentlyContinue)"
    }
    return $Watcher.Process.ExitCode
}

function Wait-ForWatcherStart {
    param([Parameter(Mandatory)]$Watcher)
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        $watcherLog = Get-Content -Raw $Watcher.StdOut -ErrorAction SilentlyContinue
        if ($watcherLog -and $watcherLog.Contains("Watching ")) {
            return
        }
        if ($Watcher.Process.HasExited) {
            throw "Watcher exited before monitoring started: $watcherLog"
        }
        Start-Sleep -Milliseconds 200
    }
    throw "Watcher did not enter monitoring mode: $(Get-Content -Raw $Watcher.StdOut -ErrorAction SilentlyContinue)"
}

function Invoke-WatcherDirect {
    param([Parameter(Mandatory)][string]$Repository, [switch]$Once)
    $watcher = Join-Path $Repository "scripts/commit-watcher.ps1"
    & $script:PowerShellExe -NoProfile -File $watcher -Once:$Once -QuietPeriodSeconds 0 -PollIntervalSeconds 1 -CodexCommand $script:FakeCodex | Out-Null
    return $LASTEXITCODE
}

try {
    New-Item -ItemType Directory -Path $script:TestRoot -Force | Out-Null
    $script:FakeCodex = Join-Path $script:TestRoot "fake-codex.ps1"
    @'
$CliArguments = $args
$receivedPrompt = [string]::Join([Environment]::NewLine, @($input | ForEach-Object { [string]$_ }))
$record = [pscustomobject]@{
    args = $CliArguments
    prompt = $receivedPrompt
    startedUtc = (Get-Date).ToUniversalTime().ToString("o")
}
$record | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:COMMIT_WATCHER_TEST_LOG -Encoding utf8
if ($env:COMMIT_WATCHER_FAKE_EXIT) { exit [int]$env:COMMIT_WATCHER_FAKE_EXIT }
exit 0
'@ | Set-Content -LiteralPath $script:FakeCodex -Encoding utf8

    $script:PreviousLog = $env:COMMIT_WATCHER_TEST_LOG
    $script:PreviousFakeExit = $env:COMMIT_WATCHER_FAKE_EXIT
    $env:COMMIT_WATCHER_TEST_LOG = $script:FakeCodexLog
    Remove-Item Env:COMMIT_WATCHER_FAKE_EXIT -ErrorAction SilentlyContinue

    # The watcher refuses all work when the initial tree is dirty.
    $dirtyRepo = New-DisposableRepository "dirty-start"
    Set-Content -LiteralPath (Join-Path $dirtyRepo "README.md") -Value "pre-existing change" -Encoding utf8
    $dirtyExit = Invoke-WatcherDirect -Repository $dirtyRepo -Once
    Assert-That ($dirtyExit -eq 2) "dirty startup should exit with code 2"
    Assert-That (-not (Test-Path -LiteralPath $script:FakeCodexLog)) "dirty startup must not invoke Codex"
    Assert-That ((Invoke-TestGit $dirtyRepo @("status", "--porcelain")).Count -gt 0) "dirty startup must preserve changes"
    Write-Host "PASS: dirty initial tree is refused without changing files"

    # Changes made after a clean startup invoke the CLI only after a full quiet period.
    $watchRepo = New-DisposableRepository "debounce"
    $watchRepoStatus = Invoke-TestGit $watchRepo @("status", "--porcelain=v1", "--untracked-files=all")
    Assert-That ([string]::IsNullOrWhiteSpace([string]::Join("`n", @($watchRepoStatus)))) "debounce test repository should start clean: $watchRepoStatus"
    $watcher = Start-WatcherProcess -Repository $watchRepo -QuietSeconds 3 -PollSeconds 1 -Once
    Wait-ForWatcherStart $watcher
    $watchedFile = Join-Path $watchRepo "src-change.py"
    Set-Content -LiteralPath $watchedFile -Value "value = 1" -Encoding utf8
    Start-Sleep -Milliseconds 1400
    $lastEdit = Get-Date
    Set-Content -LiteralPath $watchedFile -Value "value = 2" -Encoding utf8
    $debounceExit = Wait-Watcher $watcher
    Assert-That ($debounceExit -eq 2) "watcher should pause when the fake CLI leaves changes uncommitted; exit=$debounceExit; stdout=$(Get-Content -Raw $watcher.StdOut -ErrorAction SilentlyContinue); stderr=$(Get-Content -Raw $watcher.StdErr -ErrorAction SilentlyContinue)"
    $invocations = @(Get-Content -LiteralPath $script:FakeCodexLog | ForEach-Object { $_ | ConvertFrom-Json })
    Assert-That ($invocations.Count -eq 1) "debounce should invoke Codex once"
    $startedUtc = ([datetime]$invocations[0].startedUtc).ToUniversalTime()
    $lastEditUtc = $lastEdit.ToUniversalTime()
    Assert-That (($startedUtc - $lastEditUtc).TotalSeconds -ge 2.5) "Codex should start only after the final edit's quiet period"
    Assert-That ((Invoke-TestGit $watchRepo @("rev-list", "--count", "HEAD")).Trim() -eq "1") "the watcher must not commit without a working agent"
    Assert-That (Test-Path -LiteralPath $watchedFile) "the changed file must remain untouched by the fake CLI"
    $invokedArgs = [string]::Join(" ", [string[]]$invocations[0].args)
    Assert-That ($invokedArgs -notmatch "(?i)(^|\s)push(\s|$)") "the watcher must not invoke a push command"
    Write-Host "PASS: polling detects edits, debounce resets, and Codex is invoked once"

    # The agent prompt receives a no-remote instruction and a bounded dispatch snapshot.
    Assert-That ($invocations[0].prompt.Contains("Never run push, fetch, pull")) "the Codex request must forbid remote operations"
    Assert-That ($invocations[0].prompt.Contains("Expected branch:")) "the Codex request must include the expected branch"
    $watcherSource = Get-Content -Raw (Join-Path $script:SourceRoot "scripts/commit-watcher.ps1")
    Assert-That ($watcherSource -notmatch "(?im)^\s*git(\.exe)?\s+push\b") "the watcher source must never execute git push"
    Write-Host "PASS: watcher dispatch is local-only and branch-scoped"

    # A CLI failure is surfaced and does not alter or commit the worktree.
    $failureRepo = New-DisposableRepository "codex-failure"
    $env:COMMIT_WATCHER_FAKE_EXIT = "7"
    $failureWatcher = Start-WatcherProcess -Repository $failureRepo -QuietSeconds 1 -PollSeconds 1 -Once
    Wait-ForWatcherStart $failureWatcher
    Set-Content -LiteralPath (Join-Path $failureRepo "README.md") -Value "change for failing fake CLI" -Encoding utf8
    $failureExit = Wait-Watcher $failureWatcher
    Assert-That ($failureExit -eq 7) "CLI failures should be returned by the watcher"
    Assert-That ((Invoke-TestGit $failureRepo @("rev-list", "--count", "HEAD")).Trim() -eq "1") "CLI failure must not create a commit"
    Assert-That (-not [string]::IsNullOrWhiteSpace((Invoke-TestGit $failureRepo @("status", "--porcelain")))) "CLI failure must preserve dirty changes"
    Remove-Item Env:COMMIT_WATCHER_FAKE_EXIT -ErrorAction SilentlyContinue
    Write-Host "PASS: Codex errors are surfaced and changes remain uncommitted"

    # Repository state changes pause monitoring before any agent dispatch.
    Remove-Item -LiteralPath $script:FakeCodexLog -Force
    $branchRepo = New-DisposableRepository "branch-change"
    $branchWatcher = Start-WatcherProcess -Repository $branchRepo -QuietSeconds 5 -PollSeconds 1
    Wait-ForWatcherStart $branchWatcher
    Invoke-TestGit $branchRepo @("switch", "-c", "watcher-test-branch") | Out-Null
    $branchExit = Wait-Watcher $branchWatcher
    Assert-That ($branchExit -eq 2) "branch changes should pause the watcher"
    Assert-That (-not (Test-Path -LiteralPath $script:FakeCodexLog)) "branch changes must not invoke Codex"
    Write-Host "PASS: branch changes pause monitoring without dispatch"

    $lockRepo = New-DisposableRepository "index-lock"
    $lockWatcher = Start-WatcherProcess -Repository $lockRepo -QuietSeconds 5 -PollSeconds 1
    Wait-ForWatcherStart $lockWatcher
    New-Item -ItemType File -Path (Join-Path $lockRepo ".git/index.lock") -Force | Out-Null
    $lockExit = Wait-Watcher $lockWatcher
    Assert-That ($lockExit -eq 2) "an index lock should pause the watcher"
    Assert-That (-not (Test-Path -LiteralPath $script:FakeCodexLog)) "an index lock must not invoke Codex"
    Write-Host "PASS: active Git index lock pauses monitoring without dispatch"

    # A stop-file signal exercises the same graceful exit path; the Console event
    # registration separately verifies Ctrl+C support without interrupting tests.
    $shutdownRepo = New-DisposableRepository "shutdown"
    $stopFile = Join-Path $script:TestRoot "stop-watcher"
    $shutdownWatcher = Start-WatcherProcess -Repository $shutdownRepo -QuietSeconds 5 -PollSeconds 1 -StopFile $stopFile
    Wait-ForWatcherStart $shutdownWatcher
    Start-Sleep -Milliseconds 500
    New-Item -ItemType File -Path $stopFile -Force | Out-Null
    $shutdownExit = Wait-Watcher $shutdownWatcher
    Assert-That ($shutdownExit -eq 0) "graceful shutdown should return success"
    $shutdownOutput = Get-Content -Raw $shutdownWatcher.StdOut
    Assert-That ($shutdownOutput.Contains("Watcher stopped.")) "graceful shutdown must run final cleanup"
    Assert-That ($watcherSource.Contains("[Console]::add_CancelKeyPress")) "Ctrl+C handler must be registered"
    Assert-That ($watcherSource.Contains("[Console]::remove_CancelKeyPress")) "Ctrl+C handler must be removed during cleanup"
    Assert-That ((Invoke-TestGit $shutdownRepo @("rev-list", "--count", "HEAD")).Trim() -eq "1") "shutdown must not create a commit"
    Write-Host "PASS: graceful shutdown cleans up; Ctrl+C handler is registered"

    Write-Host "All disposable watcher tests passed."
}
finally {
    if ($null -eq $script:PreviousLog) { Remove-Item Env:COMMIT_WATCHER_TEST_LOG -ErrorAction SilentlyContinue }
    else { $env:COMMIT_WATCHER_TEST_LOG = $script:PreviousLog }
    if ($null -eq $script:PreviousFakeExit) { Remove-Item Env:COMMIT_WATCHER_FAKE_EXIT -ErrorAction SilentlyContinue }
    else { $env:COMMIT_WATCHER_FAKE_EXIT = $script:PreviousFakeExit }

    foreach ($watcherProcess in $script:WatcherProcesses) {
        if (-not $watcherProcess.HasExited) {
            $watcherProcess.Kill()
            $watcherProcess.WaitForExit()
        }
    }

    if (Test-Path -LiteralPath $script:TestRoot) {
        $resolvedTestRoot = (Resolve-Path $script:TestRoot).Path
        $resolvedTempRoot = (Resolve-Path ([IO.Path]::GetTempPath())).Path
        if (-not $resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to delete test directory outside the system temp directory: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
