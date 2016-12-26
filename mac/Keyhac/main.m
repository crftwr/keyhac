//
//  main.m
//  Keyhac
//
//  Created by Tomonori Shimomura on 2016/10/22.
//  Copyright © 2016年 craftware. All rights reserved.
//

#import <Cocoa/Cocoa.h>

//extern int AppMain();

//#include <wchar.h>

#if defined(_DEBUG)
#undef _DEBUG
#include "python.h"
#define _DEBUG
#else
#include "python.h"
#endif

#define PYTHON_INSTALL_PATH @"/Library/Frameworks/Python.framework/Versions/3.5"
#define KEYHAC_PROJECT_PATH @"/Users/tom/Project/keyhac"

//--------------------------------------

int AppMain( int argc, const char * argv[] )
{
    NSString * exe_dir;
    
    // Get app directory
    {
        NSString * bundle_dir;
        bundle_dir = [[NSBundle mainBundle] bundlePath];
        exe_dir = [NSString stringWithFormat:@"%@/%@", bundle_dir, @"Contents/MacOS" ];
    }

    // Setup environment variable "PATH"
    {
        NSString * env_path = [NSString stringWithUTF8String: getenv("PATH")];

#if defined(DEBUG)
        env_path = [NSString stringWithFormat:@"%@:%@", env_path, PYTHON_INSTALL_PATH ];
#else
        env_path = [NSString stringWithFormat:@"%@:%@", env_path, exe_dir ];
#endif
        
        setenv("PATH", [env_path cStringUsingEncoding:(NSUTF8StringEncoding)], 1);
    }
    
    // Python home
    {
#if defined(DEBUG)
        NSString * python_home = [NSString stringWithFormat:@"%@%@",
                                  PYTHON_INSTALL_PATH, @"/lib/python3.5"
                                  ];
#else
        NSString * python_home = exe_dir;
#endif
        Py_SetPythonHome( (wchar_t*)[python_home cStringUsingEncoding:NSUTF32LittleEndianStringEncoding] );
    }
    
    // Python module search path
    {
#if defined(DEBUG)
        NSString * python_path = [NSString stringWithFormat:@"%@%@:%@%@:%@%@:%@%@:%@%@:%@%@:%@%@:%@%@",
                                  KEYHAC_PROJECT_PATH, @"/extension",
                                  KEYHAC_PROJECT_PATH, @"",
                                  KEYHAC_PROJECT_PATH, @"/..",
                                  PYTHON_INSTALL_PATH, @"/lib/python3.5",
                                  PYTHON_INSTALL_PATH, @"/lib/python3.5/plat-darwin",
                                  PYTHON_INSTALL_PATH, @"/lib/python3.5/lib-dynload",
                                  PYTHON_INSTALL_PATH, @"/lib/python3.5/site-packages",
                                  PYTHON_INSTALL_PATH, @"/lib/python3.5/site-packages/accessibility-0.4.0-py3.5-macosx-10.6-intel.egg"
                                  ];
#else
        NSString * python_path = [NSString stringWithFormat:@"%@%@:%@%@:%@%@",
                                  exe_dir, @"/extension",
                                  exe_dir, @"/library.zip",
                                  exe_dir, @"/lib"
                                  ];
#endif
        
        Py_SetPath( (wchar_t*)[python_path cStringUsingEncoding:NSUTF32LittleEndianStringEncoding] );
    }
    
    // Initialization
    Py_Initialize();
    
    // Setup sys.argv
    {
        wchar_t * py_argv[argc];
        int py_argc = 0;
        
        NSArray *arguments = [[NSProcessInfo processInfo] arguments];
        for( int i=0 ; i<[arguments count] ; ++i )
        {
            NSString * arg = arguments[i];
            
#if defined(DEBUG)
            if(i==0)
            {
                NSString * program_name = [NSString stringWithFormat:@"%@%@",
                                          KEYHAC_PROJECT_PATH, @"/keyhac_main.py"
                                          ];
                
                py_argv[py_argc++] = (wchar_t*)[program_name cStringUsingEncoding:NSUTF32LittleEndianStringEncoding];
                continue;
            }
#endif
            
            if([arg isEqualToString:@"-NSDocumentRevisionsDebugMode"])
            {
                ++i; // Skip another argument
                continue;
            }
            
            py_argv[py_argc++] = (wchar_t*)[arg cStringUsingEncoding:NSUTF32LittleEndianStringEncoding];
        }
        
        PySys_SetArgv(py_argc, py_argv);
    }
    
    // Execute python side main script
    {
        PyObject * module = PyImport_ImportModule("keyhac_main");
        if (module == NULL)
        {
            PyErr_Print();
        }
        
        Py_XDECREF(module);
        module = NULL;
    }
    
    // Termination
    Py_Finalize();
    
    return 0;
}

int main(int argc, const char * argv[])
{
    AppMain( argc, argv );
    
    //return NSApplicationMain(argc, argv);
    return 0;
}
