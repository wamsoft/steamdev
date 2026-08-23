# Windows から WSL 内 docker の deckbuild を呼ぶ薄いラッパ
#
#   .\deckbuild.ps1 image                          # イメージ作成 (初回)
#   .\deckbuild.ps1 -Src D:\path\to\project all    # ビルド一式
#   .\deckbuild.ps1 -Src ... -Preset x64-linux -BuildType Release build
#
# CMAKEOPT は環境変数で渡す: $env:CMAKEOPT='-DFOO=ON'; .\deckbuild.ps1 ... all

param(
    [string]$Src = (Get-Location).Path,
    [string]$Preset = "x64-linux",
    [string]$BuildType = "Release",
    [Parameter(Position = 0)][string]$Command = "all"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$wslScript = (wsl -e wslpath -a ($scriptDir -replace '\\', '/')).Trim() + "/deckbuild.sh"
$wslSrc = (wsl -e wslpath -a ($Src -replace '\\', '/')).Trim()

$cmakeopt = if ($env:CMAKEOPT) { $env:CMAKEOPT } else { "" }

wsl -e bash -c "PRESET='$Preset' BUILD_TYPE='$BuildType' CMAKEOPT='$cmakeopt' bash '$wslScript' -s '$wslSrc' $Command"
exit $LASTEXITCODE
