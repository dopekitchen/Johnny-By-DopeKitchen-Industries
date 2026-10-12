$ErrorActionPreference = 'Stop'
. "$PSScriptRoot/release-version.ps1"
$cases = @(
    @{ Tags=@(); HeadTags=@(); Messages=@('chore: initial'); Expected='0.1.0' },
    @{ Tags=@('v1.2.3'); HeadTags=@(); Messages=@('fix: repair'); Expected='1.2.4' },
    @{ Tags=@('v1.2.3'); HeadTags=@(); Messages=@("Merge pull request #1`n`nfeat: add installer"); Expected='1.3.0' },
    @{ Tags=@('v1.2.3'); HeadTags=@(); Messages=@('fix!: change API'); Expected='2.0.0' },
    @{ Tags=@('v1.2.3'); HeadTags=@(); Messages=@("chore: migrate`n`nBREAKING CHANGE: schema"); Expected='2.0.0' },
    @{ Tags=@('v1.2.3'); HeadTags=@(); Messages=@('docs: update','feat(ui): add picker'); Expected='1.3.0' },
    @{ Tags=@('v1.2.3','v9.0.0-rc.1','v2.0.0'); HeadTags=@(); Messages=@('fix: patch'); Expected='2.0.1' },
    @{ Tags=@('v1.2.3'); HeadTags=@(); Messages=@('feat: release v1.4.0'); Expected='1.4.0' },
    @{ Tags=@('v1.2.3'); HeadTags=@('v1.2.3'); Messages=@('fix: retry'); Expected='1.2.3' },
    @{ Tags=@('v1.2.3'); HeadTags=@(); Messages=@('fix: literal $(throw "untrusted")'); Expected='1.2.4' }
)
foreach ($case in $cases) {
    $actual = Get-ReleaseVersion -Tags $case.Tags -HeadTags $case.HeadTags -Messages $case.Messages
    if ($actual -ne $case.Expected) { throw "Expected $($case.Expected), got $actual" }
}
foreach ($message in @('fix: release (v1.2.3)', 'fix: release (v1.0.0)')) {
    $rejected = $false
    try { Get-ReleaseVersion -Tags @('v1.2.3') -HeadTags @() -Messages @($message) } catch { $rejected = $true }
    if (-not $rejected) { throw 'Expected stale explicit version to be rejected' }
}
Write-Host 'All 12 release-version cases passed.'
