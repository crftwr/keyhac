//
//  KeyhacAppDelegate.m
//  Keyhac - Python-scriptable keyboard customization
//
//  Application delegate implementation for the Keyhac macOS app bundle.
//  Handles Python embedding and hands control to keyhac.main:main, which
//  runs the PuiKit console window, the menu-bar extra, and the CGEventTap
//  hook — all on this main thread. Structure ported from XeFM's
//  XeFMAppDelegate.m.
//

#import "KeyhacAppDelegate.h"
#include <Python.h>

@implementation KeyhacAppDelegate {
    BOOL pythonInitialized;
}

- (instancetype)init {
    self = [super init];
    if (self) {
        pythonInitialized = NO;
    }
    return self;
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSLog(@"Launching Keyhac");

    // Initialize Python interpreter
    if (![self initializePython]) {
        NSString *errorMessage = @"Failed to initialize Python interpreter.\n\n"
                                 @"Possible causes:\n"
                                 @"• Python.framework is missing or corrupted\n"
                                 @"• Keyhac source files are missing from the bundle\n"
                                 @"• Incompatible Python version\n\n"
                                 @"Please reinstall Keyhac or check Console.app for detailed error logs.";
        [self showErrorDialog:errorMessage];

        [NSApp terminate:self];
        return;
    }

    // Run Keyhac in the current process
    [self launchKeyhac];
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    [self shutdownPython];
}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    // Keyhac is an agent app (LSUIElement): closing the console window must
    // leave the hook and the menu-bar extra running. Quit comes from the
    // menu extra or the console's quit action, via keyhac.main returning.
    return NO;
}

#pragma mark - Python Management

- (BOOL)initializePython {
    // Get bundle paths
    NSBundle *mainBundle = [NSBundle mainBundle];
    // Use "Current" symlink to support any Python version
    NSString *frameworksPath = [[mainBundle privateFrameworksPath]
        stringByAppendingPathComponent:@"Python.framework/Versions/Current"];
    NSString *resourcesPath = [mainBundle resourcePath];

    // Verify Python.framework exists
    NSFileManager *fileManager = [NSFileManager defaultManager];
    if (![fileManager fileExistsAtPath:frameworksPath]) {
        NSLog(@"ERROR: Python.framework not found at path: %@", frameworksPath);
        return NO;
    }

    // Configure Python initialization
    PyConfig config;
    PyConfig_InitPythonConfig(&config);

    // Set Python home to embedded framework
    PyStatus homeStatus = PyConfig_SetBytesString(&config, &config.home,
        [frameworksPath UTF8String]);
    if (PyStatus_Exception(homeStatus)) {
        NSLog(@"ERROR: Failed to set Python home: %s", homeStatus.err_msg);
        PyConfig_Clear(&config);
        return NO;
    }

    // Set program name
    PyStatus nameStatus = PyConfig_SetBytesString(&config, &config.program_name,
        "Keyhac");
    if (PyStatus_Exception(nameStatus)) {
        NSLog(@"ERROR: Failed to set program name: %s", nameStatus.err_msg);
        PyConfig_Clear(&config);
        return NO;
    }

    // Initialize Python
    PyStatus status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);

    if (PyStatus_Exception(status)) {
        NSLog(@"ERROR: Python initialization failed: %s", status.err_msg);
        NSLog(@"ERROR: Python home was set to: %@", frameworksPath);
        return NO;
    }

    // Configure sys.path to include bundled modules. The keyhac and puikit
    // packages live at the Resources root; collected third-party deps under
    // Resources/python_packages (see build.sh).
    NSString *packagesPath = [resourcesPath
        stringByAppendingPathComponent:@"python_packages"];

    NSString *keyhacPath = [resourcesPath stringByAppendingPathComponent:@"keyhac/main.py"];
    NSString *puikitPath = [resourcesPath stringByAppendingPathComponent:@"puikit"];

    if (![fileManager fileExistsAtPath:keyhacPath]) {
        NSLog(@"ERROR: Keyhac entry module not found at: %@", keyhacPath);
        Py_Finalize();
        return NO;
    }
    if (![fileManager fileExistsAtPath:puikitPath]) {
        NSLog(@"ERROR: PuiKit library directory not found at: %@", puikitPath);
        Py_Finalize();
        return NO;
    }

    // Add paths to sys.path
    PyRun_SimpleString("import sys");

    NSString *resourcesPathCmd = [NSString stringWithFormat:@"sys.path.insert(0, '%@')", resourcesPath];
    PyRun_SimpleString([resourcesPathCmd UTF8String]);

    NSString *packagesPathCmd = [NSString stringWithFormat:@"sys.path.insert(0, '%@')", packagesPath];
    PyRun_SimpleString([packagesPathCmd UTF8String]);

    if (PyErr_Occurred()) {
        NSLog(@"ERROR: Python error occurred during sys.path configuration");
        PyErr_Print();
        Py_Finalize();
        return NO;
    }

    pythonInitialized = YES;
    NSLog(@"Python initialized successfully");
    return YES;
}

- (void)shutdownPython {
    if (pythonInitialized) {
        Py_Finalize();
        pythonInitialized = NO;
        NSLog(@"Python finalized");
    }
}

- (void)dealloc {
    [super dealloc];
}

#pragma mark - Keyhac

- (void)launchKeyhac {
    if (!pythonInitialized) {
        NSLog(@"ERROR: Cannot launch Keyhac - Python not initialized");
        exit(1);
        return;
    }

    // A sane PATH so shell_execute / launch-application actions can find
    // common CLI tools when launched from Finder (which passes a minimal env).
    [self setupEnvironmentPath];

    // Plain argv: console UI + menu-bar extra, the shipped default.
    PyRun_SimpleString("import sys");
    PyRun_SimpleString("sys.argv = ['Keyhac']");

    // Import the Keyhac entry module (Resources/keyhac/main.py). Resources/ is
    // on sys.path, so the whole keyhac package resolves from there.
    // keyhac.main.main() checks the Accessibility permission itself (prompting
    // via the system dialog) and runs the console loop until quit.
    PyObject *keyhacModule = PyImport_ImportModule("keyhac.main");
    if (!keyhacModule) {
        NSLog(@"ERROR: Failed to import keyhac.main module");
        PyErr_Print();
        exit(1);
        return;
    }

    PyObject *mainFunc = PyObject_GetAttrString(keyhacModule, "main");
    if (!mainFunc || !PyCallable_Check(mainFunc)) {
        NSLog(@"ERROR: main function not found or not callable");
        Py_XDECREF(mainFunc);
        Py_DECREF(keyhacModule);
        exit(1);
        return;
    }

    // Call main() - this blocks until Keyhac quits.
    NSLog(@"Calling keyhac.main.main()");
    PyObject *result = PyObject_CallObject(mainFunc, NULL);

    int exitCode = 0;
    if (!result) {
        NSLog(@"ERROR: main() failed");
        PyErr_Print();
        exitCode = 1;
    } else if (PyLong_Check(result)) {
        exitCode = (int)PyLong_AsLong(result);
    }

    Py_XDECREF(result);
    Py_DECREF(mainFunc);
    Py_DECREF(keyhacModule);

    NSLog(@"main() returned %d, terminating application", exitCode);

    // Use exit() instead of [NSApp terminate:self] to avoid issues when
    // running directly from the command line.
    exit(exitCode);
}

- (void)setupEnvironmentPath {
    NSString *currentPath = [[[NSProcessInfo processInfo] environment] objectForKey:@"PATH"];
    if (!currentPath) {
        currentPath = @"";
    }

    // Common locations for CLI tools.
    NSArray *additionalPaths = @[
        @"/opt/homebrew/bin",        // Homebrew (Apple Silicon)
        @"/usr/local/bin",           // Homebrew (Intel Mac)
        @"/usr/bin",                 // System binaries
        @"/bin",                     // Core system binaries
        [@"~/bin" stringByExpandingTildeInPath],                    // User binaries
        [@"~/.local/bin" stringByExpandingTildeInPath]              // Python user binaries
    ];

    NSMutableArray *pathComponents = [NSMutableArray array];

    for (NSString *path in additionalPaths) {
        BOOL isDirectory;
        if ([[NSFileManager defaultManager] fileExistsAtPath:path isDirectory:&isDirectory] && isDirectory) {
            [pathComponents addObject:path];
        }
    }

    if ([currentPath length] > 0) {
        [pathComponents addObjectsFromArray:[currentPath componentsSeparatedByString:@":"]];
    }

    // Remove duplicates while preserving order
    NSMutableArray *uniquePaths = [NSMutableArray array];
    NSMutableSet *seenPaths = [NSMutableSet set];
    for (NSString *path in pathComponents) {
        if (![seenPaths containsObject:path]) {
            [uniquePaths addObject:path];
            [seenPaths addObject:path];
        }
    }

    NSString *newPath = [uniquePaths componentsJoinedByString:@":"];
    setenv("PATH", [newPath UTF8String], 1);

    // Also update Python's os.environ so subprocess calls see the new PATH
    NSString *pythonCmd = [NSString stringWithFormat:@"import os; os.environ['PATH'] = '%@'",
                          [newPath stringByReplacingOccurrencesOfString:@"'" withString:@"\\'"]];
    PyRun_SimpleString([pythonCmd UTF8String]);
}

#pragma mark - Utility Methods

- (NSString *)getBundleResourcePath {
    NSBundle *mainBundle = [NSBundle mainBundle];
    return [mainBundle resourcePath];
}

- (void)showErrorDialog:(NSString *)message {
    NSAlert *alert = [[NSAlert alloc] init];
    [alert setMessageText:@"Keyhac Error"];
    [alert setInformativeText:message];
    [alert setAlertStyle:NSAlertStyleCritical];
    [alert addButtonWithTitle:@"OK"];
    [alert runModal];
}

@end
