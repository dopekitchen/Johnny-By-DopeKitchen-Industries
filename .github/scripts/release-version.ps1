param([string]$OutputFile = $env:GITHUB_OUTPUT)
$ErrorActionPreference = 'Stop'

function Get-ReleaseVersion {
    param([string[]]$Tags, [string[]]$HeadTags, [string[]]$Messages)
    $pattern = '^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$'
    $versions = @($Tags | Where-Object { $_ -cmatch $pattern } |
        Sort-Object { [version]$_.Substring(1) } -Descending)
    $current = @($HeadTags | Where-Object { $_ -cmatch $pattern } |
        Sort-Object { [version]$_.Substring(1) } -Descending)
    # Re-runs repair an unfinished release instead of allocating another tag.
    if ($current.Count) { return $current[0].Substring(1) }
    $latest = if ($versions.Count) { [version]$versions[0].Substring(1) } else { [version]'0.0.0' }
    $text = $Messages -join "`n"
    $explicit = [regex]::Match($Messages[0], '(?m)(?:^|[\s(])v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))(?=$|[\s)])')
    if ($explicit.Success) {
        $version = $explicit.Groups[1].Value
        if ([version]$version -le $latest) { throw "Explicit version v$version must be greater than v$latest" }
        return $version
    }
    if ($text -cmatch '(?m)^[a-z]+(?:\([^\r\n)]+\))?!: |^BREAKING[ -]CHANGE:') {
        return "$($latest.Major + 1).0.0"
    }
    if ($text -cmatch '(?m)^feat(?:\([^\r\n)]+\))?: ') {
        return "$($latest.Major).$($latest.Minor + 1).0"
    }
    if (-not $versions.Count) { return '0.1.0' }
    return "$($latest.Major).$($latest.Minor).$($latest.Build + 1)"
}

if ($MyInvocation.InvocationName -ne '.') {
    $tags = @(git tag --merged HEAD)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read release tags' }
    $headTags = @(git tag --points-at HEAD)
    $latest = $tags | Where-Object { $_ -cmatch '^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$' } |
        Sort-Object { [version]$_.Substring(1) } -Descending | Select-Object -First 1
    $range = if ($latest) { "$latest..HEAD" } else { 'HEAD' }
    # Include merge bodies and all intervening commits, not just "Merge pull request ...".
    $messages = @((git log $range --format='%B%x1e') -join "`n" -split [char]30 |
        Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read release commits' }
    if (-not $messages.Count) { $messages = @('') }
    $version = Get-ReleaseVersion -Tags $tags -HeadTags $headTags -Messages $messages
    Write-Host "Release version: v$version"
    if ($OutputFile) {
        "version=$version" >> $OutputFile
        "tag=v$version" >> $OutputFile
    }
}
