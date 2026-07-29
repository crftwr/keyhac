//
//  KeyhacAppDelegate.h
//  Keyhac - Python-scriptable keyboard customization
//
//  Application delegate for the Keyhac macOS app bundle: embeds the bundled
//  Python.framework and runs keyhac.main:main on the main thread.
//

#import <Cocoa/Cocoa.h>

@interface KeyhacAppDelegate : NSObject <NSApplicationDelegate>

- (BOOL)initializePython;
- (void)shutdownPython;
- (void)launchKeyhac;
- (NSString *)getBundleResourcePath;
- (void)showErrorDialog:(NSString *)message;

@end
