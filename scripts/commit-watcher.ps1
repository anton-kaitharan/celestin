[CmdletBinding()]
param(
    [ValidateRange(0, 86400)]
    [int]$QuietPeriodSeconds = 90,

    [ValidateRange(1, 300)]
    [int]$PollIntervalSeconds = 3,

    [string]$CodexCommand = "codex",

    [string]$StopFilePath,

    [switch]$Once
)

$ErrorActionPreference = "Stop"
$script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:StopRequested = $false

function Write-WatcherLog {
    param([Parameter(Mandatory)][string]$Message)

    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    Write-Host "[$stamp] $Message"
}

function Invoke-RepositoryGit {
    param([Parameter(Mandatory)][string[]]$GitArguments)

    $output = & git -C $script:RepositoryRoot @GitArguments 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $rendered = [string]::Join([Environment]::NewLine, @($output | ForEach-Object { [string]$_ }))
        throw "git $($GitArguments -join ' ') failed ($code): $rendered"
    }
    return [string]::Join([Environment]::NewLine, @($output | ForEach-Object { [string]$_ }))
}

function Get-RepositoryBranch {
    return (Invoke-RepositoryGit -GitArguments @("symbolic-ref", "--quiet", "--short", "HEAD")).Trim()
}

function Get-RepositoryStatus {
    return (Invoke-RepositoryGit -GitArguments @("status", "--porcelain=v1", "--untracked-files=all")).TrimEnd()
}

function Get-GitOperationBlocker {
    $gitDirectory = (Invoke-RepositoryGit -GitArguments @("rev-parse", "--git-dir")).Trim()
    if (-not [IO.Path]::IsPathRooted($gitDirectory)) {
        $gitDirectory = Join-Path $script:RepositoryRoot $gitDirectory
    }

    $indexLock = (Invoke-RepositoryGit -GitArguments @("rev-parse", "--git-path", "index.lock")).Trim()
    if (-not [IO.Path]::IsPathRooted($indexLock)) {
        $indexLock = Join-Path $script:RepositoryRoot $indexLock
    }
    if (Test-Path -LiteralPath $indexLock) {
        return "Git index lock exists: $indexLock"
    }

    foreach ($marker in @("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")) {
        $markerPath = Join-Path $gitDirectory $marker
        if (Test-Path -LiteralPath $markerPath) {
            return "Git operation is in progress: $marker"
        }
    }
    return $null
}

function Get-WorkingTreeFingerprint {
    $status = Get-RepositoryStatus
    if ([string]::IsNullOrWhiteSpace($status)) {
        return ""
    }

    # `git diff HEAD` covers both staged and unstaged tracked content. Hash each
    # untracked file separately because ordinary diffs omit untracked content.
    $trackedDiff = Invoke-RepositoryGit -GitArguments @("diff", "--binary", "HEAD", "--")
    $untracked = Invoke-RepositoryGit -GitArguments @("ls-files", "--others", "--exclude-standard")
    $untrackedFacts = [Collections.Generic.List[string]]::new()
    foreach ($relativePath in ($untracked -split "`r?`n" | Where-Object { $_ })) {
        if ($relativePath.Contains("`n") -or $relativePath.Contains("`r")) {
            throw "A changed path contains a newline; refusing an ambiguous snapshot."
        }
        $fullPath = Join-Path $script:RepositoryRoot $relativePath
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "Cannot fingerprint untracked path safely: $relativePath"
        }
        $fileHash = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash
        $untrackedFacts.Add("$relativePath=$fileHash")
    }

    $snapshot = @($status, $trackedDiff, ($untrackedFacts -join "`n")) -join "`n--snapshot-part--`n"
    $bytes = [Text.Encoding]::UTF8.GetBytes($snapshot)
    return [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes))
}

function Invoke-GranularCommitAgent {
    param(
        [Parameter(Mandatory)][string]$ExpectedBranch,
        [Parameter(Mandatory)][string]$ExpectedFingerprint,
        [Parameter(Mandatory)][string]$StatusSnapshot
    )

    $prompt = @"
Delegate this entire review and commit task to the repository-local custom subagent named `commit_granular`. If the CLI cannot invoke a custom subagent in this execution mode, the coordinator may follow the exact same instructions in `.codex/agents/commit-granular.toml` as a single-agent fallback. If a subagent starts but cannot inspect or safely operate on the repository, stop without committing.

Watcher snapshot:
- Repository: $script:RepositoryRoot
- Expected branch: $ExpectedBranch
- Working-tree fingerprint at dispatch: $ExpectedFingerprint
- `git status --porcelain=v1 --untracked-files=all` at dispatch:
```text
$StatusSnapshot
```

The subagent must recheck the repository, branch, operation state, and current diffs before acting. If the changed scope differs from this snapshot while it reviews, stop without committing. Create only coherent local commits for completed changes. Never run push, fetch, pull, remote changes, or any network operation. Never rewrite history or stage the whole tree. Follow the agent's complete safety instructions.
"@

    Write-WatcherLog "Invoking Codex CLI via `codex exec` and requesting the commit_granular subagent."
    # Explicitly enable the supported subagent mechanism for this one process.
    # workspace-write keeps the run scoped; never use a bypass or full-access mode.
    $output = $prompt | & $CodexCommand exec `
        --cd $script:RepositoryRoot `
        --sandbox workspace-write `
        -c agents.enabled=true `
        - 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host ([string]$line)
    }
    return $code
}

$script:CancelHandler = [ConsoleCancelEventHandler]{
    param($sender, $eventArgs)
    $eventArgs.Cancel = $true
    $script:StopRequested = $true
}

$exitCode = 0
[Console]::add_CancelKeyPress($script:CancelHandler)
try {
    $topLevel = (Invoke-RepositoryGit -GitArguments @("rev-parse", "--show-toplevel")).Trim()
    if (-not [string]::Equals((Resolve-Path $topLevel).Path, $script:RepositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Watcher root does not match the Git repository root."
    }

    $branchAtStart = Get-RepositoryBranch
    if ([string]::IsNullOrWhiteSpace($branchAtStart)) {
        throw "Detached HEAD is not supported; check out a branch before starting the watcher."
    }

    $blocker = Get-GitOperationBlocker
    if ($blocker) {
        Write-WatcherLog "Paused: $blocker"
        $exitCode = 2
    }
    elseif (-not [string]::IsNullOrWhiteSpace((Get-RepositoryStatus))) {
        Write-WatcherLog "Refusing startup because the working tree is already dirty. Review or commit the existing changes, then restart the watcher."
        $exitCode = 2
    }
    else {
        Write-WatcherLog "Watching $script:RepositoryRoot on branch '$branchAtStart' (quiet period ${QuietPeriodSeconds}s, poll interval ${PollIntervalSeconds}s). Press Ctrl+C to stop."
        $pendingFingerprint = ""
        $handledFingerprint = ""
        $lastChangeAt = $null

        while (-not $script:StopRequested) {
            if ($StopFilePath -and (Test-Path -LiteralPath $StopFilePath)) {
                Write-WatcherLog "Graceful stop requested by stop file."
                $script:StopRequested = $true
                break
            }

            $currentBranch = Get-RepositoryBranch
            if ($currentBranch -ne $branchAtStart) {
                Write-WatcherLog "Paused: branch changed from '$branchAtStart' to '$currentBranch'. Review the repository and restart manually."
                $exitCode = 2
                break
            }

            $blocker = Get-GitOperationBlocker
            if ($blocker) {
                Write-WatcherLog "Paused: $blocker"
                $exitCode = 2
                break
            }

            $status = Get-RepositoryStatus
            if ([string]::IsNullOrWhiteSpace($status)) {
                $pendingFingerprint = ""
                $lastChangeAt = $null
            }
            else {
                $fingerprint = Get-WorkingTreeFingerprint
                if ($fingerprint -ne $pendingFingerprint) {
                    $pendingFingerprint = $fingerprint
                    $lastChangeAt = Get-Date
                    Write-WatcherLog "Detected a changed tree; debounce timer restarted."
                }

                if ($fingerprint -ne $handledFingerprint -and
                    $lastChangeAt -and
                    ((Get-Date) - $lastChangeAt).TotalSeconds -ge $QuietPeriodSeconds) {
                    $dispatchFingerprint = $fingerprint
                    $dispatchStatus = $status
                    $agentExitCode = Invoke-GranularCommitAgent `
                        -ExpectedBranch $branchAtStart `
                        -ExpectedFingerprint $dispatchFingerprint `
                        -StatusSnapshot $dispatchStatus
                    $handledFingerprint = $dispatchFingerprint

                    $currentBranch = Get-RepositoryBranch
                    $blocker = Get-GitOperationBlocker
                    if ($currentBranch -ne $branchAtStart -or $blocker) {
                        Write-WatcherLog "Paused after agent run because repository state changed: branch='$currentBranch'; blocker='$blocker'."
                        $exitCode = 2
                        break
                    }

                    $afterStatus = Get-RepositoryStatus
                    $afterFingerprint = Get-WorkingTreeFingerprint
                    if ($agentExitCode -ne 0) {
                        Write-WatcherLog "Codex exited with code $agentExitCode; no automatic retry will occur until the tree changes."
                        $exitCode = $agentExitCode
                    }
                    elseif (-not [string]::IsNullOrWhiteSpace($afterStatus) -and $afterFingerprint -ne $dispatchFingerprint) {
                        Write-WatcherLog "Paused: the tree changed during the Codex run and remains dirty. Review it before restarting."
                        $exitCode = 2
                        break
                    }
                    else {
                        if ([string]::IsNullOrWhiteSpace($afterStatus)) {
                            Write-WatcherLog "Codex finished; the working tree is clean. Monitoring resumes."
                            $pendingFingerprint = ""
                            $lastChangeAt = $null
                        }
                    else {
                        Write-WatcherLog "Paused: Codex left changes uncommitted. Review the reported reason and restart the watcher manually after resolving it."
                        $exitCode = 2
                        break
                    }
                    }

                    if ($Once) {
                        break
                    }
                }
            }

            Start-Sleep -Seconds $PollIntervalSeconds
        }
    }
}
catch {
    Write-WatcherLog "Error: $($_.Exception.Message)"
    $exitCode = 1
}
finally {
    [Console]::remove_CancelKeyPress($script:CancelHandler)
    Write-WatcherLog "Watcher stopped."
}

exit $exitCode
