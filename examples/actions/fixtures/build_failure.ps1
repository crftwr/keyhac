# The Windows twin of build_failure.sh: a build that failed, in a terminal.
#
# Run it in Windows Terminal:
#   powershell -NoExit -ExecutionPolicy Bypass -File build_failure.ps1
#
# Carries three shapes the action has to handle - "path:line:" from a linter,
# MSVC's "path(line,col):", and a Python traceback's `File "...", line N` -
# and the last match is not the first, because "take me to the file and line"
# means the one that stopped the build.
$project = Join-Path $env:TEMP "acme"
New-Item -ItemType Directory -Force -Path (Join-Path $project "src") | Out-Null
$parser = Join-Path $project "src\parser.py"
$loader = Join-Path $project "src\loader.py"
$native = Join-Path $project "src\native.cpp"
Set-Content -Path $parser -Value (1..59 | ForEach-Object { "# line $_" })
Set-Content -Path $loader -Value (1..39 | ForEach-Object { "# line $_" })
Set-Content -Path $native -Value (1..29 | ForEach-Object { "// line $_" })
$Host.UI.RawUI.WindowTitle = "build - acme"
Write-Output "> make build"
Write-Output "acme 0.4.1 - building 3 modules"
Write-Output "${loader}:17:5: warning: unused import 'os'"
Write-Output "${native}(21,9): warning C4101: 'index': unreferenced local variable"
Write-Output ""
Write-Output "Traceback (most recent call last):"
Write-Output "  File `"$loader`", line 12, in load"
Write-Output "    return parse(text)"
Write-Output "  File `"$parser`", line 42, in parse"
Write-Output "    raise ValueError(f`"unbalanced bracket at {index}`")"
Write-Output "ValueError: unbalanced bracket at 137"
Write-Output ""
Write-Output "make: *** [build] Error 1"
Write-Output ""
Write-Output "(this window is the screen; leave it open)"
