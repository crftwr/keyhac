/*
 * main.m - Keyhac macOS Application Launcher
 *
 * Entry point for the Keyhac native macOS application: initializes
 * NSApplication and installs the delegate that embeds Python and hands
 * control to keyhac.main. A real bundle executable (rather than a generic
 * python3) is REQUIRED so the Accessibility permission the CGEventTap needs
 * attaches to a stable app identity (see doc/06-packaging.md).
 */

#import <Cocoa/Cocoa.h>
#import "KeyhacAppDelegate.h"

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        // Create the shared NSApplication instance
        NSApplication *app = [NSApplication sharedApplication];

        // The delegate handles application lifecycle events and Python embedding
        KeyhacAppDelegate *delegate = [[KeyhacAppDelegate alloc] init];
        [app setDelegate:delegate];

        // Start the main event loop. keyhac.main's PuiKit console backend and
        // the CGEventTap both service this same loop (main-thread rule).
        [app run];
    }
    return 0;
}
