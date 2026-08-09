/*
 * bridge.c - keyhac-mcp-bridge.exe, the launcher for the MCP stdio bridge
 *
 * A console-subsystem executable that runs the bundle's embedded interpreter
 * as `python.exe -m keyhac.mcp.bridge`, forwarding its own arguments, standard
 * handles and exit code. It is the exact equivalent of the
 * keyhac-mcp-bridge.cmd build.ps1 used to write - and, since 2.2.3, the thing
 * that .cmd invokes.
 *
 * WHY AN EXECUTABLE AND NOT A BATCH FILE. On the Microsoft Store (MSIX)
 * install, nothing inside the package can be launched by an ordinary process:
 * every path under C:\Program Files\WindowsApps refuses CreateProcess with
 * "Access is denied", however the ACL reads, because those binaries may only
 * be started with the package's own identity. The .cmd therefore could not
 * work there - neither directly nor through cmd.exe - and Claude Desktop's
 * MCP server died at startup with no output at all.
 *
 * The supported way out is an *app execution alias*: the package declares an
 * alias in its manifest (windows.appExecutionAlias, see build_msix.ps1),
 * Windows drops a stub in %LOCALAPPDATA%\Microsoft\WindowsApps pointing at it,
 * and launching that stub starts the target with package identity - which is
 * then allowed to run python.exe out of its own package. An alias can only
 * name an .exe in the package and cannot carry arguments, so `-m
 * keyhac.mcp.bridge` has to be baked into a real executable. This is it. (It
 * is the same mechanism the Store's Python uses for its `python.exe` alias.)
 *
 * Bundle layout it assumes, all relative to its own directory = <root>, so it
 * works wherever the folder is installed and whatever the working directory:
 *   <root>\keyhac-mcp-bridge.exe    this launcher
 *   <root>\runtime\python.exe       embeddable CPython (console interpreter)
 *   <root>\app\keyhac\              Keyhac package
 *   <root>\Lib\site-packages\       third-party deps
 *
 * python.exe, not pythonw.exe: the MCP stdio transport needs a real stdout.
 * PYTHONPATH is honoured only because build.ps1 deletes the embeddable's
 * *._pth - with that file present the interpreter ignores PYTHONPATH entirely
 * and the bridge could not find the keyhac package.
 */

#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#include <windows.h>
#include <stdio.h>   /* fwprintf, _snwprintf_s */
#include <wchar.h>   /* wcsrchr */

/* Longest path we build under the bundle root. */
#define KEYHAC_PATH_MAX 32768

/* Diagnostics go to stderr, not a message box: this is a console program whose
 * caller is an MCP client, and the client logs our stderr. A message box would
 * hang the launch behind a window nobody is looking at. */
static int fail(const wchar_t *what, DWORD code)
{
    fwprintf(stderr, L"keyhac-mcp-bridge: %ls (error %lu)\n", what, code);
    return 1;
}

/*
 * The tail of our command line, past argv[0] - forwarded to Python verbatim
 * rather than rebuilt from an argv, so the caller's quoting survives intact.
 * The bridge takes no arguments of its own; a --config for a non-default
 * config.py passes straight through.
 */
static const wchar_t *arguments_after_program(const wchar_t *cmdline)
{
    const wchar_t *cursor = cmdline;

    if (*cursor == L'"') {
        cursor++;
        while (*cursor && *cursor != L'"') { cursor++; }
        if (*cursor == L'"') { cursor++; }
    } else {
        while (*cursor && *cursor != L' ' && *cursor != L'\t') { cursor++; }
    }
    while (*cursor == L' ' || *cursor == L'\t') { cursor++; }
    return cursor;
}

int wmain(void)
{
    wchar_t root[KEYHAC_PATH_MAX];
    wchar_t pythonPath[KEYHAC_PATH_MAX];
    wchar_t commandLine[KEYHAC_PATH_MAX];
    wchar_t *lastSlash;
    const wchar_t *arguments;
    STARTUPINFOW startup;
    PROCESS_INFORMATION child;
    HANDLE job;
    DWORD status = 1;

    /* <root>, with its trailing backslash, from our own image path. */
    DWORD length = GetModuleFileNameW(NULL, root, KEYHAC_PATH_MAX);
    if (length == 0 || length >= KEYHAC_PATH_MAX) {
        return fail(L"cannot determine my own path", GetLastError());
    }
    lastSlash = wcsrchr(root, L'\\');
    if (lastSlash == NULL) {
        return fail(L"my own path has no directory", 0);
    }
    lastSlash[1] = L'\0';

    _snwprintf_s(pythonPath, KEYHAC_PATH_MAX, _TRUNCATE,
                 L"%lsapp;%lsLib\\site-packages", root, root);
    if (!SetEnvironmentVariableW(L"PYTHONPATH", pythonPath)) {
        return fail(L"cannot set PYTHONPATH", GetLastError());
    }

    arguments = arguments_after_program(GetCommandLineW());
    _snwprintf_s(commandLine, KEYHAC_PATH_MAX, _TRUNCATE,
                 L"\"%lsruntime\\python.exe\" -m keyhac.mcp.bridge%ls%ls",
                 root, *arguments ? L" " : L"", arguments);

    /* A job with KILL_ON_JOB_CLOSE so the interpreter cannot outlive us. An
     * MCP client ends a server by terminating the process it spawned - us -
     * and without this the Python child would be left holding the pipe. The
     * process is created suspended purely so it is inside the job before it
     * can run; a failure to build the job is not fatal, it only costs that
     * guarantee. */
    job = CreateJobObjectW(NULL, NULL);
    if (job != NULL) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
        ZeroMemory(&limits, sizeof(limits));
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                &limits, sizeof(limits));
    }

    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput  = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError  = GetStdHandle(STD_ERROR_HANDLE);
    ZeroMemory(&child, sizeof(child));

    if (!CreateProcessW(NULL, commandLine, NULL, NULL, TRUE,
                        CREATE_SUSPENDED, NULL, NULL, &startup, &child)) {
        DWORD failure = GetLastError();
        if (job != NULL) { CloseHandle(job); }
        return fail(L"cannot start the bundled interpreter "
                    L"(runtime\\python.exe)", failure);
    }
    if (job != NULL) { AssignProcessToJobObject(job, child.hProcess); }
    ResumeThread(child.hThread);

    WaitForSingleObject(child.hProcess, INFINITE);
    if (!GetExitCodeProcess(child.hProcess, &status)) { status = 1; }

    CloseHandle(child.hThread);
    CloseHandle(child.hProcess);
    if (job != NULL) { CloseHandle(job); }
    return (int)status;
}
