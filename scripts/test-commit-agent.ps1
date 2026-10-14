[CmdletBinding()]
param(
    [string]$CodexCommand = "codex"
)

$ErrorActionPreference = "Stop"
$script:SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("commit-agent-test-" + [guid]::NewGuid().ToString("N"))

function Invoke-TestGit {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $output = & git -C $script:TestRepo @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $output"
    }
    return [string]::Join([Environment]::NewLine, @($output | ForEach-Object { [string]$_ }))
}

try {
    New-Item -ItemType Directory -Path $script:TempRoot -Force | Out-Null
    $script:TestRepo = Join-Path $script:TempRoot "repo"
    New-Item -ItemType Directory -Path $script:TestRepo -Force | Out-Null
    Invoke-TestGit @("init", "--quiet") | Out-Null
    Invoke-TestGit @("config", "user.name", "Commit Agent Test") | Out-Null
    Invoke-TestGit @("config", "user.email", "commit-agent@example.invalid") | Out-Null

    New-Item -ItemType Directory -Path (Join-Path $script:TestRepo ".codex/agents") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:TestRepo "src") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:TestRepo "tests") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $script:TestRepo "docs") -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $script:SourceRoot ".codex/agents/commit-granular.toml") -Destination (Join-Path $script:TestRepo ".codex/agents/commit-granular.toml")
    Set-Content -LiteralPath (Join-Path $script:TestRepo "AGENTS.md") -Encoding utf8 -Value @'
For code changes, run `python -m unittest discover -s tests -v`.
Keep `scratch.txt` and `.env` unchanged and uncommitted. This test repository's
changelog requires a dated entry for every meaningful change.
'@
    Set-Content -LiteralPath (Join-Path $script:TestRepo "CHANGELOG.md") -Encoding utf8 -Value "# Test changelog`n"
    Set-Content -LiteralPath (Join-Path $script:TestRepo "src/counter.py") -Encoding utf8 -Value @'
def increment(value: int) -> int:
    return value + 1
'@
    Set-Content -LiteralPath (Join-Path $script:TestRepo "tests/test_counter.py") -Encoding utf8 -Value @'
import unittest
from src.counter import increment

class IncrementTests(unittest.TestCase):
    def test_increment(self):
        self.assertEqual(increment(2), 3)
'@
    Set-Content -LiteralPath (Join-Path $script:TestRepo "docs/demo.md") -Encoding utf8 -Value @'
# Demo

The demo runs over local stdio.
'@
    Invoke-TestGit @("add", "AGENTS.md", "CHANGELOG.md", "src/counter.py", "tests/test_counter.py", "docs/demo.md", ".codex/agents/commit-granular.toml") | Out-Null
    Invoke-TestGit @("commit", "--quiet", "-m", "chore: initialize disposable agent test") | Out-Null

    # Two independent changes plus intentionally unrelated/sensitive files.
    Set-Content -LiteralPath (Join-Path $script:TestRepo "src/counter.py") -Encoding utf8 -Value @'
def increment(value: int) -> int:
    return value + 2
'@
    Set-Content -LiteralPath (Join-Path $script:TestRepo "tests/test_counter.py") -Encoding utf8 -Value @'
import unittest
from src.counter import increment

class IncrementTests(unittest.TestCase):
    def test_increment_by_two(self):
        self.assertEqual(increment(2), 4)
'@
    Set-Content -LiteralPath (Join-Path $script:TestRepo "docs/demo.md") -Encoding utf8 -Value @'
# Demo

The demo runs over local stdio and uses an isolated temporary SQLite database.
'@
    Set-Content -LiteralPath (Join-Path $script:TestRepo "scratch.txt") -Encoding utf8 -Value "unrelated local note"
    Set-Content -LiteralPath (Join-Path $script:TestRepo ".env") -Encoding utf8 -Value "API_KEY=synthetic-not-a-secret"

    $branch = Invoke-TestGit @("branch", "--show-current")
    $statusSnapshot = Invoke-TestGit @("status", "--porcelain=v1", "--untracked-files=all")
    $prompt = @"
Delegate this task to the project-local `commit_granular` subagent. If `codex exec` cannot invoke a custom subagent in this mode, perform this task directly by following `.codex/agents/commit-granular.toml` exactly. If a subagent starts but cannot operate safely, stop without committing.

Repository: $script:TestRepo
Expected branch: $branch
This is a disposable test repository. There are two independent completed changes:
1. Change increment behavior from one to two, update its unit test, run `python -m unittest discover -s tests -v`, and record that milestone in CHANGELOG.md.
2. Update the demo documentation and record that separate documentation milestone in CHANGELOG.md.
Do not stage or commit `scratch.txt` or `.env`; they are unrelated and must remain unmodified and uncommitted. Do not run any remote or push operation.

Dispatch status:
```text
$statusSnapshot
```
Make separate, coherent commits for the independent milestones, inspect each staged diff before committing, then report the commits and checks.
"@

    Write-Host "Running actual Codex subagent in disposable repository $script:TestRepo"
    $output = $prompt | & $CodexCommand exec `
        --cd $script:TestRepo `
        --sandbox workspace-write `
        -c agents.enabled=true `
        -
    $codexExit = $LASTEXITCODE
    foreach ($line in $output) { Write-Host ([string]$line) }
    if ($codexExit -ne 0) { throw "codex exec failed with exit code $codexExit" }

    $subjects = @((Invoke-TestGit @("log", "-2", "--format=%s")) -split "`r?`n" | Where-Object { $_ })
    if ($subjects.Count -ne 2) { throw "Expected two feature commits, found $($subjects.Count): $($subjects -join '; ')" }
    $commitSets = @()
    $hashes = @((Invoke-TestGit @("log", "-2", "--format=%H")) -split "`r?`n" | Where-Object { $_ })
    foreach ($hash in $hashes) {
        $files = @((Invoke-TestGit @("show", "--format=", "--name-only", $hash)) -split "`r?`n" | Where-Object { $_ })
        $commitSets += ,$files
        if (-not ($files -contains "CHANGELOG.md")) { throw "Every meaningful commit must include CHANGELOG.md: $($files -join ', ')" }
        if ($files -contains "scratch.txt" -or $files -contains ".env") {
            throw "The agent committed an unrelated or sensitive file."
        }
    }
    $hasCodeCommit = $false
    $hasSeparateDocsCommit = $false
    foreach ($files in $commitSets) {
        if (($files -contains "src/counter.py") -and ($files -contains "tests/test_counter.py") -and -not ($files -contains "docs/demo.md")) {
            $hasCodeCommit = $true
        }
        if (($files -contains "docs/demo.md") -and -not ($files -contains "src/counter.py") -and -not ($files -contains "tests/test_counter.py")) {
            $hasSeparateDocsCommit = $true
        }
    }
    if (-not $hasCodeCommit) { throw "A code commit must include the implementation and test without the independent docs change." }
    if (-not $hasSeparateDocsCommit) { throw "The independent documentation change must have its own commit." }
    if (Test-Path -LiteralPath (Join-Path $script:TestRepo ".git/config")) {
        $remotes = Invoke-TestGit @("remote")
        if (-not [string]::IsNullOrWhiteSpace($remotes)) { throw "Disposable test repository unexpectedly has a remote." }
    }
    $remaining = Invoke-TestGit @("status", "--porcelain=v1", "--untracked-files=all")
    if (($remaining -split "`r?`n" | Where-Object { $_ -and $_ -notmatch '^\?\? (\.env|scratch\.txt)$' }).Count -gt 0) {
        throw "Unexpected remaining changes after the agent run: $remaining"
    }
    if (-not ($remaining -match '\.env') -or -not ($remaining -match 'scratch\.txt')) {
        throw "The excluded files should still be untracked: $remaining"
    }

    Write-Host "PASS: actual custom subagent created two granular commits in the disposable repository."
    Write-Host "Recent commits: $(Invoke-TestGit @('log', '-2', '--format=%h %s'))"
    Write-Host "Latest commit: $(Invoke-TestGit @('log', '-1', '--format=%h %s'))"
    Write-Host "Excluded `.env` and scratch.txt remain uncommitted; no remote is configured."
}
finally {
    if (Test-Path -LiteralPath $script:TempRoot) {
        $resolvedTempRoot = (Resolve-Path ([IO.Path]::GetTempPath())).Path
        $resolvedTestRoot = (Resolve-Path $script:TempRoot).Path
        if (-not $resolvedTestRoot.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to delete test directory outside the system temp directory: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
