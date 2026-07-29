/*
 * launcher.c - Keyhac Windows application launcher
 *
 * The Windows analog of macos_app/src/{main.m,KeyhacAppDelegate.m}: a tiny
 * native executable that embeds the CPython interpreter (python3XX.dll shipped
 * next to it, from the python.org "embeddable" package) and hands control to
 * Keyhac's Python entry point (keyhac.main:main — console window + tray +
 * WH_KEYBOARD_LL hook, all on this main thread).
 *
 * It is a GUI-subsystem program (no console window): stdout/stderr are not
 * connected to a terminal, so any fatal error before the UI is up is surfaced
 * through a MessageBox instead of being silently lost.
 *
 * Bundle layout this launcher assumes (all relative to the .exe directory =
 * <root>). The whole CPython runtime lives under runtime\ to keep the root
 * tidy; Keyhac.exe is built with a static CRT and *delay-loads* python3XX.dll,
 * so it has no load-time dependency on anything in runtime\. Before the first
 * Python call it adds runtime\ to the DLL search path
 * (SetDefaultDllDirectories + AddDllDirectory) and pre-loads python3XX.dll
 * from there; CPython's own extension loader then resolves each .pyd's sibling
 * DLLs out of runtime\ too.
 *   <root>\Keyhac.exe               this launcher (static CRT, no external DLLs)
 *   <root>\runtime\python3XX.dll   embeddable CPython + CRT + support DLLs
 *   <root>\runtime\python3XX.zip   zipped standard library
 *   <root>\runtime\*.pyd           stdlib C extensions
 *   <root>\Lib\site-packages\      third-party deps (numpy, ...)
 *   <root>\app\keyhac\             Keyhac package (main.py entry + engine)
 *   <root>\app\puikit\             PuiKit toolkit (pure Python)
 *
 * DPI: Per-Monitor-V2 awareness is declared in the embedded manifest
 * (Keyhac.manifest via Keyhac.rc), so it is fixed at process start — the hook
 * and every PuiKit window see true pixel coordinates on any display.
 */

#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00  /* Win10: SetDefaultDllDirectories / AddDllDirectory */
#endif
#include <windows.h>
#include <stdio.h>   /* _snwprintf_s */
#include <wchar.h>   /* wcsncpy_s, wcsrchr */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

/* MessageBoxW lives in user32; kernel32 (GetModuleFileNameW, MultiByteToWideChar)
 * and python3XX.lib (via pyconfig.h's #pragma) are linked automatically. */
#pragma comment(lib, "user32.lib")

/* Longest path we build under the bundle root. */
#define KEYHAC_PATH_MAX 32768

/*
 * Python bootstrap, run once the interpreter is up. Mirrors the macOS launcher:
 * import `keyhac.main` (app\keyhac\main.py) and call its main(); sys.argv was
 * already set to ("Keyhac",) via PyConfig, so the console UI is the default.
 * Any exception is turned into a MessageBox + a log file under ~/.keyhac
 * (Keyhac's user-state dir), because this is a GUI-subsystem process with no
 * console. The log deliberately does NOT go next to the exe: an installed
 * bundle's directory may be read-only to the app, and ~/.keyhac keeps the
 * diagnostic findable next to where the rest of Keyhac's state already lives.
 */
static const char *BOOTSTRAP =
    "import os, sys, traceback\n"
    "def _keyhac_run():\n"
    "    from keyhac.main import main\n"
    "    raise SystemExit(main())\n"
    "try:\n"
    "    _keyhac_run()\n"
    "except SystemExit:\n"
    "    pass\n"
    "except BaseException:\n"
    "    tb = traceback.format_exc()\n"
    "    try:\n"
    "        base = os.path.join(os.path.expanduser('~'), '.keyhac')\n"
    "        os.makedirs(base, exist_ok=True)\n"
    "        with open(os.path.join(base, 'keyhac-error.log'), 'w', encoding='utf-8') as fh:\n"
    "            fh.write(tb)\n"
    "    except Exception:\n"
    "        pass\n"
    "    try:\n"
    "        import ctypes\n"
    "        ctypes.windll.user32.MessageBoxW(None, tb, 'Keyhac failed to start', 0x10)\n"
    "    except Exception:\n"
    "        pass\n";

/* Show a fatal message box (used for failures before Python is usable). */
static void fatal_box(const wchar_t *msg)
{
    MessageBoxW(NULL, msg, L"Keyhac", MB_OK | MB_ICONERROR);
}

/* Report a PyStatus failure through a message box, including CPython's own
 * error text, then return the process exit code CPython suggests. */
static int fatal_status(const wchar_t *stage, PyStatus status)
{
    wchar_t buf[1024];
    /* status.err_msg is a narrow (UTF-8/ASCII) C string; widen it loosely. */
    wchar_t werr[512];
    werr[0] = L'\0';
    if (status.err_msg) {
        MultiByteToWideChar(CP_UTF8, 0, status.err_msg, -1, werr, 512);
    }
    _snwprintf_s(buf, 1024, _TRUNCATE,
                 L"%ls failed.\n\n%ls\n\n"
                 L"The Keyhac.exe launcher must sit next to the runtime\\ folder "
                 L"holding python3XX.dll and the python3XX.zip standard library "
                 L"from the bundle.",
                 stage, werr[0] ? werr : L"(no detail)");
    fatal_box(buf);
    return status.exitcode ? status.exitcode : 1;
}

/* Append <root>\suffix to config.module_search_paths. */
static int add_search_path(PyConfig *config, const wchar_t *root, const wchar_t *suffix)
{
    wchar_t path[KEYHAC_PATH_MAX];
    if (suffix && suffix[0]) {
        _snwprintf_s(path, KEYHAC_PATH_MAX, _TRUNCATE, L"%ls\\%ls", root, suffix);
    } else {
        _snwprintf_s(path, KEYHAC_PATH_MAX, _TRUNCATE, L"%ls", root);
    }
    PyStatus status = PyWideStringList_Append(&config->module_search_paths, path);
    return !PyStatus_Exception(status);
}

int WINAPI wWinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance,
                    PWSTR pCmdLine, int nCmdShow)
{
    (void)hInstance; (void)hPrevInstance; (void)pCmdLine; (void)nCmdShow;

    /* ---- Locate the bundle root (the directory containing Keyhac.exe) ------ */
    wchar_t exe_path[KEYHAC_PATH_MAX];
    DWORD n = GetModuleFileNameW(NULL, exe_path, KEYHAC_PATH_MAX);
    if (n == 0 || n >= KEYHAC_PATH_MAX) {
        fatal_box(L"Unable to determine the application path.");
        return 1;
    }
    /* Strip the file name to get the root directory. */
    wchar_t root[KEYHAC_PATH_MAX];
    wcsncpy_s(root, KEYHAC_PATH_MAX, exe_path, _TRUNCATE);
    wchar_t *slash = wcsrchr(root, L'\\');
    if (slash) {
        *slash = L'\0';
    }

    /* ---- Make the runtime\ folder loadable, then pre-load python3XX.dll ----
     * The interpreter DLL is delay-linked and lives in <root>\runtime, so it is
     * not resolved at process start. Add runtime\ to the secure DLL search path
     * and load python3XX.dll from there by full path (which also lets its own
     * dependencies - the CRT and support DLLs sharing that folder - resolve).
     * Doing this up front turns a missing/blocked runtime into a clear message
     * instead of an opaque delay-load crash on the first Python call. */
    wchar_t runtime_dir[KEYHAC_PATH_MAX];
    _snwprintf_s(runtime_dir, KEYHAC_PATH_MAX, _TRUNCATE, L"%ls\\runtime", root);
    SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
    AddDllDirectory(runtime_dir);

    wchar_t python_dll[KEYHAC_PATH_MAX];
    _snwprintf_s(python_dll, KEYHAC_PATH_MAX, _TRUNCATE,
                 L"%ls\\python%d%d.dll", runtime_dir, PY_MAJOR_VERSION, PY_MINOR_VERSION);
    if (!LoadLibraryExW(python_dll, NULL,
                        LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR)) {
        wchar_t msg[KEYHAC_PATH_MAX];
        _snwprintf_s(msg, KEYHAC_PATH_MAX, _TRUNCATE,
                     L"Failed to load the Python runtime:\n%ls\n\n"
                     L"The application bundle appears to be incomplete.",
                     python_dll);
        fatal_box(msg);
        return 1;
    }

    /* Derive the zipped stdlib path from the linked interpreter version, e.g.
     * "runtime\python314.zip". PY_MAJOR/PY_MINOR come from the Python headers
     * the launcher was compiled against (matched to the embeddable at build
     * time). The CPython runtime (zip + extension .pyd + their DLLs) lives
     * under runtime\ - see the layout note at the top of this file. */
    wchar_t stdlib_zip[64];
    _snwprintf_s(stdlib_zip, 64, _TRUNCATE, L"runtime\\python%d%d.zip", PY_MAJOR_VERSION, PY_MINOR_VERSION);

    /* ---- Configure CPython ------------------------------------------------ */
    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);

    /* Deterministic, self-contained interpreter. */
    config.site_import = 0;          /* no site.py global-path guessing        */
    config.user_site_directory = 0;  /* never read %APPDATA% user site-packages */
    config.write_bytecode = 0;       /* install dir may be read-only            */
    config.parse_argv = 0;           /* our argv is app args, not interp flags  */
    config.buffered_stdio = 1;

    status = PyConfig_SetString(&config, &config.program_name, exe_path);
    if (PyStatus_Exception(status)) { PyConfig_Clear(&config); return fatal_status(L"Interpreter setup", status); }

    status = PyConfig_SetString(&config, &config.home, root);
    if (PyStatus_Exception(status)) { PyConfig_Clear(&config); return fatal_status(L"Interpreter setup", status); }

    /* sys.argv = ("Keyhac",) -> console UI + tray, the shipped default. */
    {
        wchar_t *argv[] = { L"Keyhac" };
        status = PyConfig_SetArgv(&config, 1, argv);
        if (PyStatus_Exception(status)) { PyConfig_Clear(&config); return fatal_status(L"Interpreter setup", status); }
    }

    /* Explicit module search path — order matters. */
    config.module_search_paths_set = 1;
    if (!add_search_path(&config, root, stdlib_zip) ||   /* runtime\python3XX.zip  */
        !add_search_path(&config, root, L"runtime") ||    /* runtime\*.pyd + DLLs   */
        !add_search_path(&config, root, L"Lib\\site-packages") ||
        !add_search_path(&config, root, L"app")) {        /* keyhac package + puikit */
        PyConfig_Clear(&config);
        fatal_box(L"Failed to build the module search path.");
        return 1;
    }

    status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) {
        return fatal_status(L"Python initialization", status);
    }

    /* ---- Run Keyhac -------------------------------------------------------- */
    int rc = PyRun_SimpleString(BOOTSTRAP);

    if (Py_FinalizeEx() < 0) {
        rc = rc ? rc : 120;
    }
    return rc;
}
